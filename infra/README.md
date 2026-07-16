# Clinical Scribe — Cloud Deployment (Task F)

Terraform + AWS infrastructure for the AI Clinical Scribe app: a single EC2 box
running **nginx (HTTPS) in front of FastAPI/uvicorn**, backed by a **private RDS
PostgreSQL**, with all secrets in **AWS Secrets Manager**.

> **DESIGNED, NOT DEPLOYED.** Task F was time-boxed to *authoring* the
> infrastructure. Nothing here has been `terraform apply`-ed and no AWS resources
> were created. The Terraform is written to be internally consistent and
> apply-ready, but it has **not** been run. Treat the cost figures as estimates.

---

## What it provisions

| Resource | Purpose |
|---|---|
| VPC `10.0.0.0/16` | 1 public subnet (EC2) + 2 private subnets (RDS, 2 AZs) |
| Internet Gateway | Outbound/inbound for the public subnet (no NAT — saves ~$32/mo) |
| EC2 `t3.micro` (Ubuntu 22.04) | nginx + uvicorn + built React SPA |
| Elastic IP | Stable public IP so DuckDNS/Let's Encrypt keep resolving |
| RDS `db.t4g.micro` PostgreSQL 16 | All app persistence; **private**, encrypted |
| Secrets Manager secret | JSON bundle of app env vars (DB URL, JWT secret, API key) |
| IAM role + instance profile | Least-priv `GetSecretValue` on that one secret; SSM shell |
| 2 security groups | EC2 (443/80/22) and RDS (5432 **from EC2 SG only**) |

### Request flow

```
                          Elastic IP  (DuckDNS A-record -> this IP)
                               |
Browser ──HTTPS :443──▶ nginx (TLS termination, real Let's Encrypt cert)
   ▲                          │  reverse proxy, buffering OFF (SSE-safe)
   │  SSE tokens              ▼
   └──────────────── uvicorn / FastAPI  (127.0.0.1:8000, never public)
                               │  module-level async pool (5+10)
                               ▼
                     RDS PostgreSQL 16  (PRIVATE subnets, SG = EC2 SG only)
                               ▲
   FastAPI ──▶ Anthropic API (streaming + get_patient_history tool call)
```

`/auth`, `/encounters`, `/patients`, `/icd`, `/health`, `/admin` proxy to uvicorn;
everything else serves the SPA (`frontend/dist`) with `index.html` fallback.

---

## Prerequisites

1. **AWS account + credentials** on your workstation (`aws configure` or env vars).
   Terraform uses these; the *instance* never gets static keys (it uses its IAM role).
2. **Terraform >= 1.5**.
3. **A free DuckDNS subdomain** (this is how we get a real HTTPS cert for $0):
   - Go to <https://www.duckdns.org>, sign in (GitHub/Google), takes ~30 seconds.
   - Add a subdomain, e.g. `clinicalscribe` → your domain is `clinicalscribe.duckdns.org`.
   - Copy the **token** shown at the top of the dashboard.
   - You do **not** point it anywhere yet — the instance does that automatically at boot.
4. **An Anthropic API key** (for SOAP generation).
5. **A public git URL** for this repo (the instance `git clone`s it at boot).

---

## Deploy

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
#   edit terraform.tfvars: ssh_ingress_cidr, duckdns_subdomain, duckdns_token,
#   letsencrypt_email, anthropic_api_key, repo_url

terraform init
terraform plan      # review the ~20 resources
terraform apply     # type 'yes'
```

`apply` finishes in a few minutes, but the **instance keeps working for another
3–8 minutes** in the background (apt installs, npm build, DB seed, and the
certbot cert issuance). Watch progress by SSH/SSM-ing in and tailing the log:

```bash
tail -f /var/log/user-data.log
```

Terraform prints these outputs:

- `https_url` — `https://<subdomain>.duckdns.org` (open once certbot finishes)
- `ec2_public_ip` — the Elastic IP
- `rds_endpoint` — private DB endpoint (only resolves inside the VPC)
- `secret_arn`, `ssh_hint`

---

## Post-apply verification & steps

### 1. pgvector extension (may already be done)
The instance attempts `CREATE EXTENSION IF NOT EXISTS vector;` automatically. If
the log shows it failed (e.g. RDS wasn't ready yet), run it manually. From the
EC2 box (which is the only thing that can reach the private DB):

```bash
# on the instance:
source /etc/clinical-scribe.env
psql "$(echo "$DATABASE_URL" | sed 's/+asyncpg//')" -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

### 2. Verify HTTPS + the real cert
```bash
curl -I https://<subdomain>.duckdns.org/health        # expect HTTP/2 200
curl -sI http://<subdomain>.duckdns.org | grep -i location   # expect 301 -> https
```
Browser: the padlock should show a **Let's Encrypt** issuer with **no warning**
(this is the "not self-signed" requirement — the cert is publicly trusted).

### 3. Verify RDS is private
```bash
# From your laptop this MUST fail / hang (DB is not publicly reachable):
psql "postgresql://scribe@<rds_endpoint>:5432/clinical_scribe"    # should time out
```
And in the AWS console: RDS → the instance → Connectivity → **Publicly accessible = No**.

### 4. Re-run the DB bootstrap manually (if needed)
```bash
# on the instance:
cd /opt/clinical-scribe/backend
source /etc/clinical-scribe.env
.venv/bin/python init_db.py
.venv/bin/python seed.py
```

### 5. Re-run certbot manually (if first-boot issuance failed)
If DNS hadn't propagated during boot, nginx is left serving plain :80. Once
`nslookup <subdomain>.duckdns.org` returns the Elastic IP, run:
```bash
sudo certbot --nginx -d <subdomain>.duckdns.org --non-interactive --agree-tos \
  -m <your-email> --redirect
```
certbot auto-renews via the `certbot.timer` systemd timer it installs.

---

## Shell access

- **Preferred (keyless):** `aws ssm start-session --target <instance-id>` — no open
  SSH port, no key to manage. Works because the instance role has
  `AmazonSSMManagedInstanceCore`.
- **SSH:** only if you set `key_name` and your IP in `ssh_ingress_cidr`:
  `ssh ubuntu@<elastic-ip>`.

Useful once in:
```bash
sudo tail -f /var/log/user-data.log            # provisioning log
sudo systemctl status clinical-scribe          # the FastAPI service
sudo journalctl -u clinical-scribe -f          # app logs
sudo nginx -t && sudo systemctl reload nginx   # nginx config check/reload
```

---

## How the HARD requirements map to resources

| Requirement | Where it's satisfied |
|---|---|
| **Real SSE streaming, not broken by nginx** | `templates/user_data.sh.tftpl` nginx site: `proxy_buffering off`, `proxy_cache off`, `proxy_http_version 1.1`, `Connection ""`, `proxy_read_timeout 3600s`. A post-certbot `grep` verifies these survived. |
| **All persistence in AWS RDS Postgres** | `rds.tf` (`aws_db_instance`, engine `postgres` 16). `DATABASE_URL` in `secrets.tf` points the app at it; no SQLite/local files. |
| **Connection pooling** | App-side (`backend/app/db.py`, module-level async engine, pool 5+10). Deployment runs one uvicorn process so the pool is preserved; nothing here overrides it. |
| **Secrets in Secrets Manager, nothing hardcoded** | `secrets.tf` (JSON secret). `random_password` generates DB pw + JWT secret; Anthropic key is a `sensitive` var. Instance reads via IAM role → `/etc/clinical-scribe.env` (root:0600). |
| **RDS private (VPC-only)** | `rds.tf` `publicly_accessible = false` + private `db_subnet_group` (`network.tf`) + `security.tf` RDS SG ingress 5432 **from EC2 SG only** (`source_security_group_id`, not a CIDR). |
| **nginx reverse proxy, uvicorn not exposed** | uvicorn binds `127.0.0.1:8000` (systemd `ExecStart`); nginx owns 443/80; EC2 SG only opens 443/80/22. |
| **HTTPS with a real, publicly-trusted cert** | certbot `--nginx` issues a **Let's Encrypt** cert for the DuckDNS domain, with a stable Elastic IP so DNS keeps resolving. **No self-signed cert anywhere.** |

---

## Rough monthly cost (us-east-1, on-demand list price)

| Item | With 12-mo Free Tier | After Free Tier |
|---|---|---|
| EC2 `t3.micro` (730h) | **$0** (750h/mo free) | ~$7.50 |
| RDS `db.t4g.micro` (730h) | **$0** (750h/mo free) | ~$12–13 |
| EBS gp3 20 GB (root) | ~$0 (30 GB free) | ~$1.60 |
| RDS gp3 20 GB | ~$0 (20 GB free) | ~$2.30 |
| Secrets Manager (1 secret) | ~$0.40 | ~$0.40 |
| Elastic IP (attached, running) | **$0** | **$0** |
| Data transfer | negligible for a demo | negligible |
| DuckDNS + Let's Encrypt cert | **$0** | **$0** |
| **Approx. total** | **~$0.40/mo** | **~$24–25/mo** |

Costs we deliberately AVOIDED (and why we don't need them for a single-box demo):
NAT Gateway (~$32/mo — RDS needs no outbound, EC2 uses the IGW directly),
ALB/NLB (~$16/mo — single instance, nginx does TLS), Route53 hosted zone
(~$0.50/mo — DuckDNS is free), ACM+public LB (free cert but needs a LB).

> **Stop billing when done:**
> ```bash
> terraform destroy
> ```
> With `skip_final_snapshot = true` and `deletion_protection = false`, teardown is
> clean (the database is discarded — expected for a demo, dangerous in prod).

---

## Operator TODO checklist (values you must supply)

- [ ] `ssh_ingress_cidr` — your public IP `/32` (or a placeholder if using SSM)
- [ ] `duckdns_subdomain` + `duckdns_token` — from your free DuckDNS account
- [ ] `letsencrypt_email` — contact for cert notices
- [ ] `anthropic_api_key` — your Anthropic key (else generation fails)
- [ ] `repo_url` — public clone URL of this repo
- [ ] After apply: confirm `CREATE EXTENSION vector` ran (step 1 above)
- [ ] After apply: verify HTTPS padlock + that RDS is not publicly reachable

## Notes / assumptions

- The instance clones the repo over **public HTTPS git** (no deploy key wired up).
  For a private repo, add credentials/a deploy key in `user_data` or bake an AMI.
- Terraform **state contains the generated DB password + JWT secret in plaintext**.
  It's gitignored here; for a team, use an encrypted remote backend (e.g. S3 + DynamoDB lock).
- Single instance = single point of failure and brief downtime on redeploys. That's
  the intended cost/simplicity tradeoff for a take-home, not a production HA design.

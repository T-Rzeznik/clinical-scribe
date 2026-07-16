import { useState } from "react";
import {
  createEncounter,
  generateNote,
  saveVersion,
  searchIcd,
} from "./api.js";

// Split the streamed markdown note into the four SOAP fields. The model emits
// section headers like "**Subjective:**"; we find each header and take the text
// up to the next one. This is the client-side "parse the blob into 4 fields"
// step — the provider then reviews/edits each field before it's saved, so what
// lands in note_versions is human-approved, not raw model output.
function parseSoap(text) {
  const sections = { subjective: "", objective: "", assessment: "", plan: "" };
  const labels = ["subjective", "objective", "assessment", "plan"];
  // Match a header like "**Subjective:**" or "Subjective:" at a line start.
  const re = /(^|\n)\s*\**\s*(subjective|objective|assessment|plan)\s*\**\s*:?\**\s*/gi;
  const marks = [];
  let m;
  while ((m = re.exec(text)) !== null) {
    marks.push({ key: m[2].toLowerCase(), end: re.lastIndex });
  }
  for (let i = 0; i < marks.length; i++) {
    const start = marks[i].end;
    const stop = i + 1 < marks.length ? marks[i + 1].end - 0 : text.length;
    // stop should be the START of the next header, not its end — recompute:
    const next = i + 1 < marks.length ? findHeaderStart(text, marks[i + 1].end) : text.length;
    sections[marks[i].key] = text.slice(start, next).trim();
  }
  // Fallback: if nothing parsed, dump everything into Subjective so it's not lost.
  if (labels.every((k) => !sections[k])) sections.subjective = text.trim();
  return sections;
}
// Walk backward from a header's end to where its label began (so a section stops
// before the NEXT header rather than swallowing it).
function findHeaderStart(text, headerEnd) {
  const slice = text.slice(0, headerEnd);
  const idx = slice.search(/\n\s*\**\s*(subjective|objective|assessment|plan)\s*\**\s*:?\**\s*$/i);
  return idx === -1 ? headerEnd : idx;
}

export default function Workspace({ onAuthLost }) {
  const [patient, setPatient] = useState({ first: "", last: "", dob: "" });
  const [transcript, setTranscript] = useState("");

  const [encounterId, setEncounterId] = useState(null);
  const [streaming, setStreaming] = useState(false);
  const [streamed, setStreamed] = useState("");
  const [soap, setSoap] = useState(null); // {subjective, objective, assessment, plan}
  const [savedMsg, setSavedMsg] = useState("");
  const [error, setError] = useState("");

  const [icdQuery, setIcdQuery] = useState("");
  const [icdResults, setIcdResults] = useState([]);
  const [chosenCodes, setChosenCodes] = useState([]);

  function setP(field, value) {
    setPatient((p) => ({ ...p, [field]: value }));
  }

  async function handleGenerate() {
    setError("");
    setSavedMsg("");
    setSoap(null);
    setStreamed("");
    try {
      // Start (or reuse) the encounter, then stream against it.
      const enc = await createEncounter({
        patient_first_name: patient.first,
        patient_last_name: patient.last,
        patient_dob: patient.dob,
        transcript_text: transcript,
      });
      setEncounterId(enc.id);
      setStreaming(true);

      let full = "";
      await generateNote(enc.id, {
        onText: (chunk) => {
          full += chunk;
          setStreamed(full); // live-render each chunk as it arrives
        },
        onDone: () => {
          setStreaming(false);
          setSoap(parseSoap(full)); // hand off to the editable review fields
        },
        onError: (msg) => {
          setStreaming(false);
          setError(msg);
        },
      });
    } catch (err) {
      setStreaming(false);
      setError(err.message);
    }
  }

  async function handleSave() {
    setError("");
    try {
      const v = await saveVersion(encounterId, soap);
      setSavedMsg(`Saved as version ${v.version_number}.`);
    } catch (err) {
      // A 401 here means the session expired — bounce to login (draft stays in
      // the fields until then; a fuller build would preserve it across re-login).
      if (String(err.message).includes("401")) onAuthLost();
      else setError(err.message);
    }
  }

  async function handleIcdSearch(e) {
    e.preventDefault();
    if (!icdQuery.trim()) return;
    try {
      setIcdResults(await searchIcd(icdQuery));
    } catch (err) {
      setError(err.message);
    }
  }

  function toggleCode(row) {
    setChosenCodes((cur) =>
      cur.find((c) => c.code === row.code)
        ? cur.filter((c) => c.code !== row.code)
        : [...cur, row]
    );
  }

  const canGenerate =
    patient.first && patient.last && patient.dob && transcript.trim().length >= 15;

  return (
    <div className="workspace">
      <section className="card">
        <h2>New encounter</h2>
        <div className="patient-row">
          <label>
            First name
            <input value={patient.first} onChange={(e) => setP("first", e.target.value)} />
          </label>
          <label>
            Last name
            <input value={patient.last} onChange={(e) => setP("last", e.target.value)} />
          </label>
          <label>
            Date of birth
            <input type="date" value={patient.dob} onChange={(e) => setP("dob", e.target.value)} />
          </label>
        </div>
        <label>
          Encounter transcript / notes
          <textarea
            rows={7}
            placeholder="Paste the visit transcript or freeform notes…"
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
          />
        </label>
        <button onClick={handleGenerate} disabled={!canGenerate || streaming}>
          {streaming ? "Generating…" : "Generate SOAP note"}
        </button>
        {!canGenerate && (
          <p className="hint">
            Fill patient fields and at least ~15 characters of transcript to generate.
          </p>
        )}
        {error && <p className="error">{error}</p>}
      </section>

      {(streaming || streamed) && (
        <section className="card">
          <h2>{streaming ? "Streaming from Claude…" : "Generated note"}</h2>
          <pre className="stream">{streamed || "…"}</pre>
        </section>
      )}

      {soap && (
        <section className="card">
          <h2>Review &amp; edit</h2>
          <p className="hint">
            Edit each section so it reflects what you stand behind, then save.
          </p>
          {["subjective", "objective", "assessment", "plan"].map((key) => (
            <label key={key} className="soap-field">
              {key[0].toUpperCase() + key.slice(1)}
              <textarea
                rows={3}
                value={soap[key]}
                onChange={(e) => setSoap((s) => ({ ...s, [key]: e.target.value }))}
              />
            </label>
          ))}
          <button onClick={handleSave}>Save note version</button>
          {savedMsg && <p className="saved">{savedMsg}</p>}
        </section>
      )}

      {soap && (
        <section className="card">
          <h2>ICD-10 codes</h2>
          <form className="icd-search" onSubmit={handleIcdSearch}>
            <input
              placeholder="Search a symptom or diagnosis (e.g. chest pain)"
              value={icdQuery}
              onChange={(e) => setIcdQuery(e.target.value)}
            />
            <button type="submit">Search</button>
          </form>
          <ul className="icd-list">
            {icdResults.map((row) => {
              const picked = chosenCodes.find((c) => c.code === row.code);
              return (
                <li key={row.code}>
                  <button
                    className={picked ? "chip picked" : "chip"}
                    onClick={() => toggleCode(row)}
                  >
                    <strong>{row.code}</strong> {row.description}
                  </button>
                </li>
              );
            })}
          </ul>
          {chosenCodes.length > 0 && (
            <p className="chosen">
              Selected: {chosenCodes.map((c) => c.code).join(", ")}
            </p>
          )}
          <p className="hint">
            Fallback keyword search (pgvector semantic search wires in later).
          </p>
        </section>
      )}
    </div>
  );
}

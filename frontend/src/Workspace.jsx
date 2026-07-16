import { useState, useRef, useEffect } from "react";
import {
  createEncounter,
  generateNote,
  listPatientEncounters,
  listVersions,
  saveVersion,
  searchIcd,
  searchPatients,
  validateIcdCodes,
} from "./api.js";

// Pull the model's trailing "SUGGESTED_ICD_CODES: ..." line out of the streamed
// note. Returns the raw code strings plus the note text with that line removed,
// so the codes never leak into the editable SOAP fields.
function extractSuggestedCodes(text) {
  const m = text.match(/SUGGESTED_ICD_CODES:\s*(.*)\s*$/im);
  if (!m) return { codes: [], cleaned: text };
  const list = m[1].trim();
  const codes =
    list.toLowerCase() === "none"
      ? []
      : list.split(",").map((c) => c.trim()).filter(Boolean);
  const cleaned = text.slice(0, m.index).trimEnd();
  return { codes, cleaned };
}

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

export default function Workspace() {
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
  const [aiSuggested, setAiSuggested] = useState([]); // codes the AI proposed (provenance)

  // Patient picker state. `patientQuery` is what's typed in the search box;
  // `patientMatches` are the provider's matching patients; `pickedPatient` is set
  // once an existing patient is chosen (so we show a confirmed banner + can clear).
  const [patientQuery, setPatientQuery] = useState("");
  const [patientMatches, setPatientMatches] = useState([]);
  const [pickedPatient, setPickedPatient] = useState(null);
  const searchTimer = useRef(null);
  const pickerRef = useRef(null);

  // Close the patient dropdown when the user clicks anywhere outside the picker,
  // so the suggestions never sit over the fields below. A click ON a match is
  // inside pickerRef, so it still registers before this clears the list.
  useEffect(() => {
    function onDocMouseDown(e) {
      if (pickerRef.current && !pickerRef.current.contains(e.target)) {
        setPatientMatches([]);
      }
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, []);

  // Read-only history for the picked patient: their prior encounters, and the
  // saved versions of whichever encounter is expanded.
  const [history, setHistory] = useState([]);
  const [openEncounterId, setOpenEncounterId] = useState(null);
  const [historyVersions, setHistoryVersions] = useState([]);

  function setP(field, value) {
    setPatient((p) => ({ ...p, [field]: value }));
  }

  // Debounced search as the provider types. We wait 250ms after the last keystroke
  // so we don't fire a request per character, and only search at 2+ chars.
  function onPatientQueryChange(value) {
    setPatientQuery(value);
    setPickedPatient(null); // typing again means they're re-searching
    if (searchTimer.current) clearTimeout(searchTimer.current);
    if (value.trim().length < 2) {
      setPatientMatches([]);
      return;
    }
    searchTimer.current = setTimeout(async () => {
      try {
        setPatientMatches(await searchPatients(value));
      } catch {
        setPatientMatches([]); // search failure shouldn't block the New-patient path
      }
    }, 250);
  }

  // Choose an existing patient: fill the identity fields from the stored record so
  // createEncounter dedupes to the SAME patient row (no fragmented history). Then
  // load that patient's prior encounters for the history panel.
  async function choosePatient(p) {
    setPatient({ first: p.first_name, last: p.last_name, dob: p.dob });
    setPickedPatient(p);
    setPatientQuery(`${p.first_name} ${p.last_name}`);
    setPatientMatches([]);
    setOpenEncounterId(null);
    setHistoryVersions([]);
    try {
      setHistory(await listPatientEncounters(p.id));
    } catch {
      setHistory([]); // history is a bonus panel; never block the workflow
    }
  }

  // "＋ New patient": clear any prior pick and reveal the blank identity fields.
  function startNewPatient() {
    setPickedPatient(null);
    setPatient({ first: "", last: "", dob: "" });
    setPatientQuery("");
    setPatientMatches([]);
    setHistory([]);
    setOpenEncounterId(null);
    setHistoryVersions([]);
  }

  // Expand/collapse a prior encounter, loading its saved versions on first open.
  async function toggleEncounterHistory(encId) {
    if (openEncounterId === encId) {
      setOpenEncounterId(null);
      setHistoryVersions([]);
      return;
    }
    setOpenEncounterId(encId);
    setHistoryVersions([]);
    try {
      setHistoryVersions(await listVersions(encId));
    } catch {
      setHistoryVersions([]);
    }
  }

  async function handleGenerate() {
    setError("");
    setSavedMsg("");
    setSoap(null);
    setStreamed("");
    setChosenCodes([]); // fresh encounter → clear any prior selection
    setAiSuggested([]);
    setIcdResults([]);
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
        onReset: () => {
          // The model narrated a tool call ("I'll look up prior notes…"); discard
          // that so only the real note remains once it streams next.
          full = "";
          setStreamed("");
        },
        onDone: async () => {
          setStreaming(false);
          // Split the AI's suggested-codes line off the note before parsing SOAP.
          const { codes, cleaned } = extractSuggestedCodes(full);
          setStreamed(cleaned); // hide the machine line from the displayed note
          setSoap(parseSoap(cleaned)); // hand off to the editable review fields
          if (codes.length) {
            try {
              // Validate against our catalog; only recognized codes are kept, and
              // they pre-populate the Selected tray for the provider to adjust.
              const valid = await validateIcdCodes(codes);
              setAiSuggested(valid);
              setChosenCodes(valid);
            } catch {
              /* validation is best-effort — provider can still search + add */
            }
          }
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
      const v = await saveVersion(encounterId, soap, chosenCodes);
      const codeNote = chosenCodes.length
        ? ` with ${chosenCodes.length} ICD code${chosenCodes.length > 1 ? "s" : ""}`
        : "";
      setSavedMsg(`Saved as version ${v.version_number}${codeNote}.`);
    } catch (err) {
      // api.js already tried to refresh + retry on a 401, and bounced to login
      // if the refresh itself failed. So anything reaching here is a genuine
      // save error — the draft stays in the fields for another try.
      setError(err.message);
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

  // Clear the whole workspace for the next patient (back-to-back appointments) —
  // no need to log out. Guards against silently discarding an unsaved note.
  function resetWorkspace() {
    if (
      soap &&
      !savedMsg &&
      !window.confirm(
        "Start a new encounter? The current note hasn't been saved and will be cleared."
      )
    ) {
      return;
    }
    setPatient({ first: "", last: "", dob: "" });
    setTranscript("");
    setEncounterId(null);
    setStreamed("");
    setSoap(null);
    setSavedMsg("");
    setError("");
    setIcdQuery("");
    setIcdResults([]);
    setChosenCodes([]);
    setAiSuggested([]);
    setPatientQuery("");
    setPatientMatches([]);
    setPickedPatient(null);
    setHistory([]);
    setOpenEncounterId(null);
    setHistoryVersions([]);
  }

  const canGenerate =
    patient.first && patient.last && patient.dob && transcript.trim().length >= 15;

  return (
    <div className="workspace">
      <div className="col-input">
      <section className="card">
        <div className="card-head">
          <h2>New encounter</h2>
          <button
            className="link"
            onClick={resetWorkspace}
            disabled={streaming}
            title="Clear everything and start a fresh encounter for the next patient"
          >
            Clear / next patient
          </button>
        </div>

        {/* Patient picker: find an existing patient to avoid minting a duplicate
            (which would silently fragment their history), or start a new one. */}
        <div className="patient-picker" ref={pickerRef}>
          <label>
            Find patient
            <input
              placeholder="Type a name to search your patients…"
              value={patientQuery}
              onChange={(e) => onPatientQueryChange(e.target.value)}
            />
          </label>
          {patientMatches.length > 0 && (
            <ul className="patient-matches">
              {patientMatches.map((p) => (
                <li key={p.id}>
                  <button className="match" onClick={() => choosePatient(p)}>
                    <strong>
                      {p.first_name} {p.last_name}
                    </strong>
                    <span className="dob">DOB {p.dob}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {pickedPatient && (
            <p className="picked-patient">
              Using existing patient:{" "}
              <strong>
                {pickedPatient.first_name} {pickedPatient.last_name}
              </strong>{" "}
              (DOB {pickedPatient.dob}).{" "}
              <button className="link" onClick={startNewPatient}>
                Clear
              </button>
            </p>
          )}
          {!pickedPatient && (
            <p className="hint">
              Search an existing patient above, or enter a new one in the fields
              below.
            </p>
          )}
        </div>

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

      {pickedPatient && history.length > 0 && (
        <section className="card">
          <h2>
            Prior encounters — {pickedPatient.first_name}{" "}
            {pickedPatient.last_name}
          </h2>
          <ul className="history-list">
            {history.map((enc) => (
              <li key={enc.id} className="history-item">
                <button
                  className="history-head"
                  onClick={() => toggleEncounterHistory(enc.id)}
                >
                  <span>
                    {new Date(enc.created_at).toLocaleString()} ·{" "}
                    {enc.version_count > 0 ? "Saved" : "Draft (no note)"}
                  </span>
                  <span className="hint">
                    {enc.version_count} version
                    {enc.version_count === 1 ? "" : "s"}{" "}
                    {openEncounterId === enc.id ? "▲" : "▼"}
                  </span>
                </button>
                {openEncounterId === enc.id && (
                  <div className="history-versions">
                    {historyVersions.length === 0 ? (
                      <p className="hint">No saved versions for this encounter.</p>
                    ) : (
                      historyVersions.map((v) => (
                        <div key={v.id} className="history-version">
                          <div className="hv-head">
                            <strong>Version {v.version_number}</strong>
                            <span className="hint">
                              {new Date(v.saved_at).toLocaleString()}
                            </span>
                          </div>
                          {["subjective", "objective", "assessment", "plan"].map(
                            (k) => (
                              <p key={k} className="hv-field">
                                <em>{k[0].toUpperCase() + k.slice(1)}:</em>{" "}
                                {v[k]}
                              </p>
                            )
                          )}
                          {v.icd_codes.length > 0 && (
                            <p className="hv-codes">
                              ICD:{" "}
                              {v.icd_codes.map((c) => c.code).join(", ")}
                            </p>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      </div>

      <div className="col-output">
      {!streaming && !streamed && !soap && (
        <section className="card placeholder">
          <p className="hint">
            Generate a SOAP note and it will appear here for review.
          </p>
        </section>
      )}

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
          <div className="soap-grid">
            {["subjective", "objective", "assessment", "plan"].map((key) => (
              <label key={key} className="soap-field">
                {key[0].toUpperCase() + key.slice(1)}
                <textarea
                  rows={4}
                  value={soap[key]}
                  onChange={(e) => setSoap((s) => ({ ...s, [key]: e.target.value }))}
                />
              </label>
            ))}
          </div>
        </section>
      )}

      {soap && (
        <section className="card">
          <h2>ICD-10 codes</h2>

          {/* Selected tray — always visible so picks don't vanish behind a new
              search. These are what gets saved with the note version. */}
          <div className="icd-tray">
            <div className="tray-head">
              <strong>Selected codes</strong>
              {aiSuggested.length > 0 && (
                <span className="hint">
                  {aiSuggested.length} pre-filled from the AI's suggestions — review
                  and adjust.
                </span>
              )}
            </div>
            {chosenCodes.length === 0 ? (
              <p className="hint">
                None selected yet. Accept an AI suggestion or search below.
              </p>
            ) : (
              <ul className="tray-list">
                {chosenCodes.map((c) => (
                  <li key={c.code} className="tray-chip">
                    <span>
                      <strong>{c.code}</strong> {c.description}
                    </span>
                    <button
                      className="remove"
                      title="Remove"
                      onClick={() => toggleCode(c)}
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

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
                    {picked && " ✓"}
                  </button>
                </li>
              );
            })}
          </ul>
          <p className="hint">
            Fallback keyword search (pgvector semantic search wires in later).
          </p>

          <div className="save-bar">
            <button onClick={handleSave}>Save note version</button>
            {savedMsg && <p className="saved">{savedMsg}</p>}
          </div>
        </section>
      )}
      </div>
    </div>
  );
}

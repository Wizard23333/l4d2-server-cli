import { useEffect, useState } from 'react';
import { confirmAction } from './confirm';

type Field = { name: string; flag: string | null; required: boolean; choices: Array<string | number> | null; boolean: boolean; repeat: boolean; default: string | number | boolean | null; help: string; file: boolean };
type Command = { id: string; fields: Field[]; mutating: boolean; preview: boolean; background: boolean };
type Room = { id: string; label: string };

export default function OperationsPanel({ rooms, loadState }: { rooms: Room[]; loadState: (message?: string) => Promise<void> }) {
  const [commands, setCommands] = useState<Command[]>([]);
  const [selected, setSelected] = useState('room status');
  const [values, setValues] = useState<Record<string, string | boolean | string[]>>({});
  const [room, setRoom] = useState(localStorage.getItem('l4d2-room') || rooms[0]?.id || 'main');
  const [plan, setPlan] = useState<{ confirmation: string; expires: number } | null>(null);
  const [output, setOutput] = useState('Choose an operation to inspect its current values or preview a change.');
  const [busy, setBusy] = useState(false);
  const [background, setBackground] = useState(false);
  const [downloadUrl, setDownloadUrl] = useState('');

  function headers(json = false) {
    const result: Record<string, string> = { 'X-L4D2-Room': room };
    const csrf = document.cookie.split('; ').find((p) => p.startsWith('l4d2_csrf='))?.slice(10);
    if (csrf) result['X-CSRF-Token'] = csrf;
    if (json) result['Content-Type'] = 'application/json';
    return result;
  }
  useEffect(() => {
    fetch('/api/commands').then(async (response) => {
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || 'Cannot load operations');
      setCommands(payload.commands);
    }).catch((e) => setOutput(e.message));
  }, []);
  const command = commands.find((c) => c.id === selected);

  function changeCommand(next: string) {
    setSelected(next); setValues({}); setPlan(null); setOutput(''); setBackground(false); setDownloadUrl('');
    if (next.startsWith('server ')) setRoom('main');
  }
  async function upload(field: Field, file: File | undefined) {
    if (!file) return;
    setBusy(true); setPlan(null);
    try {
      const body = new FormData(); body.append('file', file);
      const response = await fetch('/api/files', { method: 'POST', headers: headers(), body });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || 'Upload failed');
      setValues((old) => ({ ...old, [field.name]: payload.upload_id }));
      setOutput(`Uploaded ${payload.original}`);
    } catch (error) { setOutput(error instanceof Error ? error.message : 'Upload failed'); }
    finally { setBusy(false); }
  }
  async function execute(preview: boolean) {
    if (!command) return;
    if (!preview && command.mutating && !(await confirmAction('Apply the reviewed change to the selected room(s)?'))) return;
    setBusy(true);
    try {
      const response = await fetch('/api/operation', { method: 'POST', headers: headers(true),
        body: JSON.stringify({ command: command.id, values, room, preview, background, ...(preview ? {} : plan || {}) }) });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(JSON.stringify({ message: payload.message || 'Operation failed', data: payload.data }, null, 2));
      setOutput(JSON.stringify(payload.data, null, 2));
      setDownloadUrl(payload.download_url || '');
      setPlan(payload.confirmation ? { confirmation: payload.confirmation, expires: payload.expires } : null);
      if (!preview) await loadState('Refreshing...');
    } catch (error) { setOutput(error instanceof Error ? error.message : 'Operation failed'); setPlan(null); }
    finally { setBusy(false); }
  }
  return <section className="panel operations-panel">
    <div className="panel-head"><div><h2>Operations</h2><p>Manage rooms, administrators, console, backups, network and diagnostics.</p></div></div>
    <div className="filter-row">
      <label>Room<select aria-label="Operation room" disabled={selected.startsWith('server ')} value={room} onChange={(e) => { const next = e.target.value; setRoom(next); localStorage.setItem('l4d2-room', next); setPlan(null); loadState('Refreshing...').catch((e) => setOutput(e.message)); }}>
        {rooms.map((r) => <option key={r.id} value={r.id}>{r.label} ({r.id})</option>)}
      </select></label>
      <label>Operation<select aria-label="Operation" value={selected} onChange={(e) => changeCommand(e.target.value)}>
        {commands.map((c) => <option key={c.id} value={c.id}>{c.id}</option>)}
      </select></label>
    </div>
    {selected.startsWith('server ') && <p>Server commands always target the main room. Use room commands to manage another room.</p>}
    {(selected.startsWith('network ') || selected === 'repair transaction') && <p>This operation applies to the host shared by all rooms.</p>}
    <div className="operation-fields">
      {command?.fields.filter((f) => f.name !== 'room').map((field) => <label key={field.name}>
        <span>{field.name.replace(/_/g, ' ')}{field.required ? ' *' : ''}</span>
        {field.file ? <input aria-label={field.name} type="file" onChange={(e) => upload(field, e.target.files?.[0])} />
          : field.boolean ? <input aria-label={field.name} type="checkbox" checked={values[field.name] === true} onChange={(e) => { setValues({ ...values, [field.name]: e.target.checked }); setPlan(null); }} />
          : field.choices ? <select aria-label={field.name} value={String(values[field.name] || '')} onChange={(e) => { setValues({ ...values, [field.name]: e.target.value }); setPlan(null); }}>
            <option value="">{field.default == null ? 'Choose...' : `Default: ${field.default}`}</option>
            {field.choices.map((choice) => <option value={choice} key={String(choice)}>{choice}</option>)}
          </select>
          : <input aria-label={field.name} value={String(values[field.name] || '')} placeholder={field.default == null ? '' : String(field.default)} onChange={(e) => { setValues({ ...values, [field.name]: field.repeat ? e.target.value.split(',').map((s) => s.trim()).filter(Boolean) : e.target.value }); setPlan(null); }} />}
        {field.help && <small>{field.help}</small>}
      </label>)}
    </div>
    <div className="actions">
      <button disabled={busy} onClick={() => execute(true)}>{busy ? 'Working...' : command?.mutating ? 'Preview change' : 'Inspect'}</button>
      {command?.mutating && <>
        {command.background && <label><input type="checkbox" checked={background} onChange={(e) => setBackground(e.target.checked)} /> Run as background task</label>}
        <button className="danger" disabled={busy || !plan} onClick={() => execute(false)}>Apply reviewed change</button>
      </>}
    </div>
    <pre className="operation-output" role="status">{output}</pre>
    {downloadUrl && <a href={downloadUrl}>Download result</a>}
  </section>;
}

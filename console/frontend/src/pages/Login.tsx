import { useState } from "react";
import { login, loginLocal, logout, type Session } from "../auth";

export default function Login({ session, denied }: { session: Session; denied: string | null }) {
  return (
    <div className="login">
      <div className="card">
        <div className="login-brand">
          <img src="/logo.svg" alt="AGS Backup Console" />
        </div>
        <div className="login-title">Accedi alla console</div>
        {denied ? (
          <>
            <div className="alert" role="alert">
              Accesso non consentito ({denied}). L'utente deve essere abilitato da un amministratore.
            </div>
            <button className="btn" onClick={() => void logout()}>Cambia utente</button>
          </>
        ) : session.mode === "dev" ? (
          <DevLogin />
        ) : (
          <>
            {session.oidcError ? (
              <div className="notice">Accesso Microsoft non disponibile: {session.oidcError}</div>
            ) : (
              <button className="btn primary" onClick={() => void login()}>Accedi con Microsoft</button>
            )}
            {session.localLogin && (
              <>
                {!session.oidcError && <div className="login-divider"><span>oppure</span></div>}
                <LocalLogin />
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function LocalLogin() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await loginLocal(email.trim(), password, code.trim());
    } catch (err) {
      setError((err as Error).message);
      setCode("");
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <label className="field">Email
        <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>
      <label className="field">Password
        <input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
      </label>
      <label className="field">Codice dell'app di autenticazione
        <input type="text" inputMode="numeric" autoComplete="one-time-code" required pattern="[0-9]{6}" maxLength={6}
          value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} className="mono otp" />
      </label>
      {error && <div className="alert" role="alert">{error}</div>}
      <button className="btn primary" type="submit" disabled={busy}>{busy ? "Accesso…" : "Accedi"}</button>
    </form>
  );
}

function DevLogin() {
  const [email, setEmail] = useState("");
  return (
    <form
      className="form-row"
      onSubmit={(e) => {
        e.preventDefault();
        void login(email.trim());
      }}
    >
      <div className="notice" style={{ width: "100%" }}>
        Modalità sviluppo: nessuna password, si entra con l'email di un utente registrato.
      </div>
      <input
        type="email" required placeholder="email@azienda.it" value={email}
        onChange={(e) => setEmail(e.target.value)} aria-label="Email"
      />
      <button className="btn primary" type="submit">Entra</button>
    </form>
  );
}

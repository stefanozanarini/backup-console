import { useState } from "react";
import { login, logout, type Session } from "../auth";

export default function Login({ session, denied }: { session: Session; denied: string | null }) {
  const [email, setEmail] = useState("");

  return (
    <div className="login">
      <div className="card">
        <div className="brand">
          <img src="/favicon.svg" alt="" width={22} height={22} />
          Backup Console
        </div>
        {denied && (
          <div className="alert" role="alert">
            Accesso non consentito ({denied}). L'utente deve essere abilitato da un amministratore.
          </div>
        )}
        {denied ? (
          <button className="btn" onClick={() => void logout()}>Cambia utente</button>
        ) : session.mode === "oidc" ? (
          <button className="btn primary" onClick={() => void login()}>Accedi con Microsoft</button>
        ) : (
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
        )}
      </div>
    </div>
  );
}

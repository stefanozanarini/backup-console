package main

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"

	"backupconsole/agent/internal/pack"
)

// buildPack: pacchetto di raccolta dalla cartella packs/, firmato. Si verifica subito con
// lo stesso codice dell'agente, così un pacchetto che l'agente rifiuterebbe non esce.
func buildPack(priv ed25519.PrivateKey, version, src, out string) {
	var def struct {
		Probes []pack.Probe `json:"probes"`
	}
	raw, err := os.ReadFile(filepath.Join(src, "probes.json"))
	if err != nil {
		log.Fatal(err)
	}
	if err := json.Unmarshal(raw, &def); err != nil {
		log.Fatalf("probes.json: %v", err)
	}
	b := pack.Bundle{Format: pack.Format, Version: version, Probes: def.Probes, Files: map[string]string{}}
	for _, p := range def.Probes {
		for _, f := range p.Scripts {
			if _, ok := b.Files[f]; ok {
				continue
			}
			if filepath.Base(f) != f {
				log.Fatalf("probe %s: nome di file non valido %q", p.ID, f)
			}
			data, err := os.ReadFile(filepath.Join(src, "scripts", f))
			if err != nil {
				log.Fatal(err)
			}
			b.Files[f] = string(data)
		}
	}
	bundle, err := json.MarshalIndent(b, "", " ")
	if err != nil {
		log.Fatal(err)
	}
	sig := base64.StdEncoding.EncodeToString(ed25519.Sign(priv, bundle))
	if _, err := pack.Parse(bundle, sig, priv.Public().(ed25519.PublicKey)); err != nil {
		log.Fatalf("pacchetto non valido: %v", err)
	}
	if err := os.MkdirAll(out, 0o755); err != nil {
		log.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(out, "collector-pack.json"), bundle, 0o644); err != nil {
		log.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(out, "collector-pack.sig"), []byte(sig+"\n"), 0o644); err != nil {
		log.Fatal(err)
	}
	ids := make([]string, 0, len(b.Probes))
	for _, p := range b.Probes {
		ids = append(ids, p.ID)
	}
	sort.Strings(ids)
	fmt.Printf("pacchetto di raccolta %s: %d probe %v, %d file, %d byte\n", version, len(ids), ids, len(b.Files), len(bundle))
}

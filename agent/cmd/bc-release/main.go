// bc-release: chiavi e firma dei rilasci dell'agente.
//
//	bc-release keygen -out release-signing.key
//	    crea la chiave privata (da tenere fuori dalla console: CI o postazione sicura)
//	    e stampa la chiave pubblica da incorporare nell'agente in build:
//	    -ldflags "-X backupconsole/agent/internal/upgrade.PublicKey=<pubblica>"
//
//	bc-release sign -key release-signing.key -version 0.2.0 dist/
//	    firma i binari bc-agent-<piattaforma>[.exe] e i QPKG della stessa versione in dist/
//	    e scrive dist/manifest.json, da caricare in console insieme ai file.
//
//	bc-release pack -key release-signing.key -version 1.0.0 -src ../packs -out dist/
//	    costruisce il pacchetto di raccolta da packs/probes.json e packs/scripts/, lo firma e
//	    scrive dist/collector-pack.json e dist/collector-pack.sig, da caricare in console.
package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

var (
	assetRe = regexp.MustCompile(`^bc-agent-((linux|windows)-(amd64|arm64|armv7))(\.exe)?$`)
	// I QPKG servono solo per l'installazione (download dalla console), non per gli aggiornamenti.
	qpkgRe = regexp.MustCompile(`^BackupConsoleAgent_(.+)_(x86_64|arm_64|arm-x41|arm-x31)\.qpkg$`)
)

type asset struct {
	SHA256    string `json:"sha256"`
	Size      int64  `json:"size"`
	Signature string `json:"signature"`
}

type manifest struct {
	Version string           `json:"version"`
	Assets  map[string]asset `json:"assets"`
}

func main() {
	log.SetFlags(0)
	if len(os.Args) < 2 {
		log.Fatal("uso: bc-release keygen|sign|pack|pubkey ...")
	}
	switch os.Args[1] {
	case "keygen":
		fs := flag.NewFlagSet("keygen", flag.ExitOnError)
		out := fs.String("out", "release-signing.key", "file della chiave privata")
		_ = fs.Parse(os.Args[2:])
		pub, priv, err := ed25519.GenerateKey(rand.Reader)
		if err != nil {
			log.Fatal(err)
		}
		if err := os.WriteFile(*out, []byte(base64.StdEncoding.EncodeToString(priv)+"\n"), 0o600); err != nil {
			log.Fatal(err)
		}
		fmt.Fprintf(os.Stderr, "chiave privata in %s (non committarla, non copiarla sulla console)\n", *out)
		fmt.Println(base64.StdEncoding.EncodeToString(pub))
	case "pubkey":
		fs := flag.NewFlagSet("pubkey", flag.ExitOnError)
		key := fs.String("key", "release-signing.key", "file della chiave privata")
		_ = fs.Parse(os.Args[2:])
		fmt.Println(base64.StdEncoding.EncodeToString(loadKey(*key).Public().(ed25519.PublicKey)))
	case "sign":
		fs := flag.NewFlagSet("sign", flag.ExitOnError)
		key := fs.String("key", "", "file della chiave privata (oppure variabile BC_RELEASE_KEY)")
		ver := fs.String("version", "", "versione del rilascio")
		_ = fs.Parse(os.Args[2:])
		if *ver == "" || fs.NArg() != 1 {
			log.Fatal("uso: bc-release sign -key FILE -version X.Y.Z DIR")
		}
		sign(loadKey(*key), *ver, fs.Arg(0))
	case "pack":
		fs := flag.NewFlagSet("pack", flag.ExitOnError)
		key := fs.String("key", "", "file della chiave privata (oppure variabile BC_RELEASE_KEY)")
		ver := fs.String("version", "", "versione del pacchetto (X.Y.Z)")
		src := fs.String("src", "../packs", "cartella con probes.json e scripts/")
		out := fs.String("out", "dist", "cartella di uscita")
		_ = fs.Parse(os.Args[2:])
		if *ver == "" {
			log.Fatal("uso: bc-release pack -key FILE -version X.Y.Z [-src DIR] [-out DIR]")
		}
		buildPack(loadKey(*key), *ver, *src, *out)
	default:
		log.Fatalf("comando sconosciuto %q", os.Args[1])
	}
}

func loadKey(path string) ed25519.PrivateKey {
	raw := os.Getenv("BC_RELEASE_KEY")
	if path != "" {
		b, err := os.ReadFile(path)
		if err != nil {
			log.Fatal(err)
		}
		raw = string(b)
	}
	b, err := base64.StdEncoding.DecodeString(strings.TrimSpace(raw))
	if err != nil || len(b) != ed25519.PrivateKeySize {
		log.Fatal("chiave privata non valida")
	}
	return ed25519.PrivateKey(b)
}

func sign(priv ed25519.PrivateKey, version, dir string) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		log.Fatal(err)
	}
	m := manifest{Version: version, Assets: map[string]asset{}}
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		var key string
		if m := assetRe.FindStringSubmatch(e.Name()); m != nil {
			key = m[1]
		} else if m := qpkgRe.FindStringSubmatch(e.Name()); m != nil && m[1] == version {
			key = "qpkg-" + m[2]
		} else {
			continue
		}
		data, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			log.Fatal(err)
		}
		sum := sha256.Sum256(data)
		m.Assets[key] = asset{
			SHA256: hex.EncodeToString(sum[:]), Size: int64(len(data)),
			Signature: base64.StdEncoding.EncodeToString(ed25519.Sign(priv, data)),
		}
	}
	if len(m.Assets) == 0 {
		log.Fatalf("nessun binario bc-agent-<piattaforma> in %s", dir)
	}
	b, _ := json.MarshalIndent(m, "", "  ")
	if err := os.WriteFile(filepath.Join(dir, "manifest.json"), b, 0o644); err != nil {
		log.Fatal(err)
	}
	names := make([]string, 0, len(m.Assets))
	for k := range m.Assets {
		names = append(names, k)
	}
	sort.Strings(names)
	fmt.Printf("firmati %d binari (%s), manifest in %s\n", len(names), strings.Join(names, ", "), filepath.Join(dir, "manifest.json"))
}

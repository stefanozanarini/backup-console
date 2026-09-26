// Package identity gestisce la chiave privata e il certificato client (mTLS) dell'agente.
// La chiave viene generata sul dispositivo e non lascia mai il dispositivo.
package identity

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"os"
	"path/filepath"
)

const (
	keyFile  = "agent.key"
	certFile = "agent.crt"
	caFile   = "ca.crt"
)

// NewCSR genera una nuova chiave (salvata in dir) e la relativa CSR in PEM.
// Il CN è indicativo: la console impone come CN l'agent_id.
func NewCSR(dir, commonName string) (string, error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return "", err
	}
	der, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return "", err
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})
	if err := os.WriteFile(filepath.Join(dir, keyFile), keyPEM, 0o600); err != nil {
		return "", err
	}
	csr, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{
		Subject: pkix.Name{CommonName: commonName},
	}, key)
	if err != nil {
		return "", err
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: csr})), nil
}

// Save scrive il certificato ricevuto dalla console e la CA che l'ha firmato.
func Save(dir, certPEM, caPEM string) error {
	if err := os.WriteFile(filepath.Join(dir, certFile), []byte(certPEM), 0o600); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, caFile), []byte(caPEM), 0o644)
}

func Load(dir string) (tls.Certificate, error) {
	cert, err := tls.LoadX509KeyPair(filepath.Join(dir, certFile), filepath.Join(dir, keyFile))
	if err != nil {
		return tls.Certificate{}, fmt.Errorf("identità agente non disponibile in %s: %w", dir, err)
	}
	return cert, nil
}

package system

import (
	"net"
	"strings"
)

// NIC è una scheda di rete attiva con i suoi indirizzi IPv4 (niente loopback, niente
// indirizzi link-local 169.254.x.x assegnati quando manca il DHCP).
type NIC struct {
	Name string   `json:"name"`
	MAC  string   `json:"mac,omitempty"`
	IPv4 []string `json:"ipv4"`
}

// Adattatori virtuali che non interessano per riconoscere la macchina in rete.
var skipNIC = []string{"loopback", "isatap", "teredo", "vethernet (wsl", "docker", "br-", "veth", "virbr"}

func networks() []NIC {
	ifs, err := net.Interfaces()
	if err != nil {
		return nil
	}
	var out []NIC
	for _, it := range ifs {
		if it.Flags&net.FlagUp == 0 || it.Flags&net.FlagLoopback != 0 {
			continue
		}
		name := strings.ToLower(it.Name)
		skip := false
		for _, p := range skipNIC {
			if strings.HasPrefix(name, p) || strings.Contains(name, p) {
				skip = true
				break
			}
		}
		if skip {
			continue
		}
		addrs, _ := it.Addrs()
		n := NIC{Name: it.Name, MAC: it.HardwareAddr.String()}
		for _, a := range addrs {
			ipn, ok := a.(*net.IPNet)
			if !ok {
				continue
			}
			ip := ipn.IP.To4()
			if ip == nil || ip.IsLinkLocalUnicast() || ip.IsLoopback() {
				continue
			}
			n.IPv4 = append(n.IPv4, ip.String())
		}
		if len(n.IPv4) > 0 {
			out = append(out, n)
		}
	}
	return out
}

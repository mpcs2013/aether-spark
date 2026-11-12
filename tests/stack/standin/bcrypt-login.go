// bcrypt-login writes one login line for rest-server's --htpasswd-file: the name given as its only
// argument, then the bcrypt hash of the password read from standard input (never an argument).
// Test-only, for the stand-in backup server (tests/stack/run.sh, layer3.sh); built inside
// rest-server's own module, which already depends on golang.org/x/crypto.
package main

import (
	"bufio"
	"fmt"
	"os"
	"strings"

	"golang.org/x/crypto/bcrypt"
)

func main() {
	if len(os.Args) != 2 || os.Args[1] == "" {
		fmt.Fprintln(os.Stderr, "usage: bcrypt-login NAME < password")
		os.Exit(2)
	}
	in := bufio.NewScanner(os.Stdin)
	if !in.Scan() {
		fmt.Fprintln(os.Stderr, "bcrypt-login: no password on standard input")
		os.Exit(2)
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(strings.TrimRight(in.Text(), "\r")), bcrypt.DefaultCost)
	if err != nil {
		fmt.Fprintln(os.Stderr, "bcrypt-login:", err)
		os.Exit(1)
	}
	fmt.Printf("%s:%s\n", os.Args[1], hash)
}

/*
  pagerduty-tui Nix Flake
  ========================

  Rust TUI:

    nix build              # Build the Rust TUI binary
    nix run                # Build and run the TUI
    nix develop            # Rust dev shell with rust-analyzer

  Python CLI:

    nix run .#pdcli                     # List incidents
    nix run .#pdcli -- -a               # Ack all triggered incidents
    nix run .#pdcli -- -b               # Daemon: auto-ack (default 3 min)
    nix run .#pdcli -- -b -i 5          # Daemon: auto-ack with 5 min interval
    nix run .#pdcli -- --test-alert     # Test notifications
    nix develop .#python                 # Python dev shell

  Requirements:

    - Cargo.lock must be tracked in git (not gitignored)
    - Files must be staged in git for Nix to see them
    - Config: ~/.config/pagerduty_tui.yaml
*/
{
  description = "Minimalist PagerDuty TUI";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, rust-overlay }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        overlays = [ (import rust-overlay) ];
        pkgs = import nixpkgs {
          inherit system overlays;
        };

        rustToolchain = pkgs.rust-bin.stable.latest.default.override {
          extensions = [ "rust-src" "rust-analyzer" ];
        };

        # Native dependencies needed for reqwest/openssl
        nativeBuildInputs = with pkgs; [
          pkg-config
          rustToolchain
        ];

        buildInputs = with pkgs; [
          openssl
        ] ++ pkgs.lib.optionals pkgs.stdenv.isDarwin [
          libiconv
        ];

        # Python environment for pd-cli.py
        pythonEnv = pkgs.python3.withPackages (ps: with ps; [
          requests
          pyyaml
        ]);

      in
      {
        packages.default = pkgs.rustPlatform.buildRustPackage {
          pname = "pagerduty-tui";
          version = "0.8.0";

          src = ./.;

          cargoLock = {
            lockFile = ./Cargo.lock;
          };

          inherit nativeBuildInputs buildInputs;

          # Set OpenSSL environment variables
          OPENSSL_NO_VENDOR = 1;

          meta = with pkgs.lib; {
            description = "Minimalist PagerDuty TUI";
            homepage = "https://github.com/Mk555/pagerduty-tui";
            license = licenses.mit;
            mainProgram = "pagerduty-tui";
          };
        };

        devShells.default = pkgs.mkShell {
          inherit nativeBuildInputs buildInputs;

          OPENSSL_NO_VENDOR = 1;

          shellHook = ''
            echo "pagerduty-tui development shell"
            echo "Rust: $(rustc --version)"
          '';
        };

        devShells.python = pkgs.mkShell {
          packages = [ pythonEnv ];

          shellHook = ''
            echo "pd-cli Python development shell"
            echo "Python: $(python --version)"
          '';
        };

        # Python CLI wrapper script
        packages.pd-cli = pkgs.writeShellApplication {
          name = "pd-cli";
          runtimeInputs = [ pythonEnv ];
          text = ''
            exec python ${./pd-cli.py} "$@"
          '';
        };
      }
    );
}

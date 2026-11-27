# Ardour 9.8 pinned build environment (qualification route, issue #78).
# Nix dev shell supplying every mandatory pkg-config dependency from
# docs/daw-integration/ardour-build-preflight.md §4.1, so the pinned waf
# build runs without host package changes (no root on this runner).
{
  description = "Ardour 9.8 pinned source build environment (riff #78)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs = { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in {
      devShells.${system}.default = pkgs.mkShell {
        packages = with pkgs; [
          # toolchain + build drivers
          gcc
          binutils
          pkg-config
          python3
          git
          gettext
          itstool
          # mandatory libraries (preflight §4.1)
          glib
          glibmm_2_4
          libsndfile
          libarchive
          curl
          liblo
          taglib
          vamp-plugin-sdk
          rubberband
          libxml2
          aubio
          libsamplerate
          libsigcxx_2_0
          lv2
          serd
          sord
          sratom
          lilv
          flac
          libogg
          fftwFloat
          alsa-lib
          readline
          boost
          # GUI-side unconditional children (preflight §3.1: no --no-gui switch)
          pango
          cairo
          cairomm_1_0
          pangomm_1_4
          fontconfig
          libX11
          libXext
          libjpeg_turbo
        ];
      };
    };
}

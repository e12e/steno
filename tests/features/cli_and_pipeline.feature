Feature: Idiomatic CLI Argument Parsing, Unified Entrypoint Dispatch, and Pipeline DRY Helpers
  As a user of Møtereferat (both via Desktop GUI and CLI)
  I want consistent argparse-driven flag handling, automatic speaker counting, and exportable HTML transcripts
  So that every flag works identically in transcribe.py and mote_app.py without code duplication.

  Scenario: Unified entrypoint routes --check-hw and --profile to CLI via idiomatic argparse
    Given the unified app entrypoint receives "--check-hw --profile light"
    When entrypoint dispatches the arguments
    Then it executes cli_main without opening the GUI window
    And stdout contains JSON keys "hardware" and "resource_fit"

  Scenario: Unified entrypoint ignores macOS LaunchServices -psn argument and opens GUI
    Given the unified app entrypoint receives "-psn_0_123456"
    When entrypoint dispatches the arguments
    Then it launches the GUI main function

  Scenario: Unified entrypoint runs --self-test via argparse
    Given the unified app entrypoint receives "--self-test"
    When entrypoint dispatches the arguments
    Then it returns exit code 0 and prints "SELF_TEST_OK"

  Scenario: Speaker count is inferred from speaker_names_hint when min and max speakers are omitted
    Given speaker_names_hint is "Kari Nordmann, Ola Hansen, Ingrid Berg"
    And min_speakers and max_speakers are empty
    When speaker bounds are resolved via resolve_speaker_bounds
    Then min_speakers is 3 and max_speakers is 3

  Scenario: Browser speaker rename export in transcript.html supports Step 5 roundtrip
    Given a generated transcript HTML file for a 2-speaker meeting
    When inspecting the generated transcript HTML content
    Then it contains the "saveUpdatedHtml()" export function and "Lagre HTML" button

  Scenario: Desktop GUI app.html uses progressive disclosure to avoid visual clutter
    Given the desktop GUI template app.html is loaded
    When inspecting the app.html layout structure
    Then advanced settings and context textareas are placed in collapsible details drawers with a live summary pill
    And all required pywebview DOM element IDs are preserved

  Scenario: CLI parser and desktop GUI support English and Norwegian localization
    Given the CLI parser is built with ui_lang "en" and "no"
    When formatting CLI help output for both languages
    Then the English help text contains "Steno - a modern annalist for your meetings - an automaton stenographer" and "100% local meeting transcription" and "--ui-lang"
    And the Norwegian help text contains "Steno - a modern annalist for your meetings - an automaton stenographer" and "100% lokal møtetranskripsjon" and "--ui-lang"
    And app.html contains the English and Norwegian UI localization toggle "setUiLanguage"

  Scenario: Cross-platform microphone capture and public release page under www/rec
    Given cross-platform ffmpeg microphone commands are generated for darwin, win32, and linux
    When inspecting the generated ffmpeg arguments and the www/rec release folder
    Then darwin uses "avfoundation", win32 uses "dshow", and linux uses "pulse" or "alsa"
    And www/rec/index.html and www/rec/steno.py exist with "Steno" and "an automaton stenographer" in The Chronicle broadsheet style

  Scenario: Zero-Python execution and universal installers for macOS, Linux, and Windows
    Given a target machine without python3 pre-installed
    When inspecting the polyglot header of dist/steno.py and the universal installers in www/rec
    Then dist/steno.py uses "# 2>nul & goto :windows_boot" so both /bin/sh and cmd.exe boot without python3
    And www/rec/install.sh installs the steno launcher into a custom prefix using /bin/sh
    And www/rec/install.ps1 and www/rec/steno.cmd provide zero-Python Windows installation and double-click launch

  Scenario: Cosmopolitan APE steno.com binary, install/uninstall lifecycle, and GUI File menu uninstall dialog
    Given the Cosmopolitan APE binary dist/steno.com and www/rec/steno.com
    When running install_steno_app and uninstall_steno_app in an isolated prefix and inspecting the GUI File menu
    Then dist/steno.com has a valid MZqFpD APE + 64-bit PE32+ header and embeds the steno.py payload
    And install_steno_app creates the cached app directory, CLI launcher, and desktop entry, and uninstall_steno_app removes them cleanly
    And the desktop GUI includes a File menu with an Uninstall option and a Yes/No confirmation modal connected to MoteApi.uninstall_app

  Scenario: Multi-architecture support for Android Termux (aarch64-linux-android), Linux (arm64/amd64), and Windows (arm64/amd64)
    Given target environments for Android Termux (aarch64-linux-android), Linux (arm64 and amd64), and Windows 11 (arm64 and amd64)
    When resolving CPU architecture, POSIX shell paths, Termux package bootstrap, and PE32+ import tables in steno.com
    Then detect_cpu_arch normalizes arm64 and amd64 across Linux, macOS, Termux, and Windows WOW64
    And Termux (aarch64-linux-android) resolves $PREFIX/bin/sh, uses pulse audio capture, and executes steno.com without /bin/sh or uv wheels
    And the Windows PE32+ code section in steno.com imports KERNEL32.dll and msvcrt.dll to extract and launch steno.py on amd64 and arm64






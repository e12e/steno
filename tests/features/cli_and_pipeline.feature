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

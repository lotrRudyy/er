# piano_riddle

Canonical CLI for piano calibration tools. It consolidates signature building, reduced-model export, and header generation behind `python -m er1.tools.piano_riddle`.

## Commands
- Build full signatures: `python -m er1.tools.piano_riddle build --log piano_calibration.log --out out_dir`
- Reduced model: `python -m er1.tools.piano_riddle reduce --log piano_calibration.log --out model_reduced.json`
- Export header: `python -m er1.tools.piano_riddle export-header --compact out_dir/model_compact.json --out out_dir/model_esp.h`

Defaults mirror the legacy scripts (gate-sigma/tolerances/cluster counts) so inputs/outputs remain compatible. The legacy `build_signatures.py` and `build_reduced_signatures.py` wrappers forward to the new CLI.

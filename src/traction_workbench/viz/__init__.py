"""Plot data for engineering views (NumPy only, no GUI dependency).

Every function here derives its numbers from the production model
(``DriveKernel``/``evaluate_point``/``PolicyEvaluator``); nothing adds physics
beyond the steady-state fundamental dq model.  Representations such as phase
waveforms (inverse Park) or SVPWM duty cycles (average model, min-max
zero-sequence injection) are deterministic re-expressions of the same
fundamental quantities and are labelled as such.
"""

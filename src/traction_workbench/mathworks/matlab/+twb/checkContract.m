function checkContract(C)
%CHECKCONTRACT  Refuse a contract whose conventions differ from the ones this implementation computes.
%   The evaluator implements amplitude-invariant, phase-peak dq quantities with d on the PM flux, mechanical
%   speed input in rpm, pole PAIRS and the electrical->mechanical positive power sign.  A package that declares
%   anything else is refused instead of being re-interpreted.
if ~isfield(C, 'schema') || ~strcmp(C.schema, 'twb-mathworks-contract/1')
    error('twb:schema', 'unknown contract schema');
end
want = {'park', 'amplitude_invariant'; 'dq_quantities', 'phase_peak'; 'd_axis', 'd_on_PM'; ...
        'speed_input', 'mechanical_rpm'; 'pole_count', 'pole_pairs'; ...
        'power_sign', 'electrical_to_mechanical_positive'};
for k = 1:size(want, 1)
    n = want{k, 1};
    if ~isfield(C.conventions, n) || ~strcmp(C.conventions.(n), want{k, 2})
        got = '(missing)';
        if isfield(C.conventions, n)
            got = C.conventions.(n);
        end
        error('twb:convention', 'convention %s: the package declares %s, this implementation computes %s', ...
              n, got, want{k, 2});
    end
end
end

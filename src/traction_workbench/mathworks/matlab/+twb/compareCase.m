function out = compareCase(c, R, C)
%COMPARECASE  One case: the target's result R against layer 2 (the Python reference values) and layer 1 (the
%   oracle values of the accepted source), quantity by quantity and status by status.
%   Numbers: |target - reference| <= atol + rtol * max(|target|, |reference|) with the contract's class
%   tolerances; "not defined" (null / NaN) must match "not defined".  Statuses (evaluable, reasons, constraint
%   states, energy mode, violated groups, claim support) must be equal - sets where the order carries no meaning.
%   out.status: PASS | FAIL | NOT_SUPPORTED (the quantity or case is outside the native port, never a pass).
out.case_id = c.case_id;
out.kind = c.kind;
out.failures = {};
out.not_supported = {};
out.checks = 0;
switch c.kind
    case 'forward'
        out = forwardChecks(out, c, R, C);
    case 'flux_lookup'
        out = check(out, 'covered', isequal(logical(R.covered), logical(c.python.covered)), R.covered, c.python.covered);
        out = check(out, 'reason', sameText(R.reason, c.python.reason), R.reason, c.python.reason);
        for q = {'psi_d_Wb', 'psi_q_Wb'}
            out = number(out, ['python:' q{1}], R.(q{1}), c.python.(q{1}), tol(C, 'Wb'));
        end
        orcs = twb.asCell(c.oracle);
        for k = 1:numel(orcs)
            o = orcs{k};
            if strcmp(o.kind, 'definition')
                out = check(out, 'oracle:covered', isequal(logical(R.covered), logical(o.covered)), R.covered, o.covered);
                out = number(out, 'oracle:psi_d_Wb', R.psi_d_Wb, o.psi_d_Wb, [o.tolerance.atol o.tolerance.rtol]);
                out = number(out, 'oracle:psi_q_Wb', R.psi_q_Wb, o.psi_q_Wb, [o.tolerance.atol o.tolerance.rtol]);
            end
        end
    case 'requirement_witness'
        p = c.python;
        out = check(out, 'witness_accepted', isequal(logical(R.witness_accepted), logical(p.witness_accepted)), ...
                    R.witness_accepted, p.witness_accepted);
        out = check(out, 'claim_support', strcmp(R.claim_support, p.claim_support), R.claim_support, p.claim_support);
        out = check(out, 'reasons', sameSet(R.reasons, p.reasons), R.reasons, p.reasons);
        out = check(out, 'violated_groups', sameSet(R.violated_groups, p.violated_groups), R.violated_groups, ...
                    p.violated_groups);
        out = number(out, 'python:Tshaft_Nm', R.Tshaft_Nm, p.Tshaft_Nm, tol(C, 'Nm'));
        out = number(out, 'python:torque_residual_Nm', R.torque_residual_Nm, p.torque_residual_Nm, tol(C, 'Nm'));
        out = number(out, 'python:torque_tolerance_Nm', R.torque_tolerance_Nm, p.torque_tolerance_Nm, tol(C, 'Nm'));
end
if ~isempty(out.failures)
    out.status = 'FAIL';
else
    out.status = 'PASS';
end
end

function out = forwardChecks(out, c, R, C)
p = c.python;
uns = twb.asCell(c.unsupported);
q = fieldnames(C.quantities);
for k = 1:numel(q)
    if any(strcmp(uns, q{k}))
        out.not_supported{end + 1} = q{k};
        continue
    end
    out = number(out, ['python:' q{k}], R.(q{k}), p.(q{k}), tol(C, C.quantities.(q{k}).class));
end
out = check(out, 'evaluable', isequal(logical(R.evaluable), logical(p.evaluable)), R.evaluable, p.evaluable);
out = check(out, 'reason', sameText(R.reason, p.reason), R.reason, p.reason);
out = check(out, 'issues', sameSet(R.issues, p.issues), R.issues, p.issues);
if any(strcmp(uns, 'accepted'))
    out.not_supported{end + 1} = 'accepted';
else
    out = check(out, 'accepted', isequal(logical(R.accepted), logical(p.accepted)), R.accepted, p.accepted);
    out = check(out, 'gate_reasons', sameSet(R.gate_reasons, p.gate_reasons), R.gate_reasons, p.gate_reasons);
end
if any(strcmp(uns, 'energy_mode'))
    out.not_supported{end + 1} = 'energy_mode';
else
    out = check(out, 'energy_mode', sameText(R.energy_mode, p.energy_mode), R.energy_mode, p.energy_mode);
end
out = check(out, 'violated_groups', sameSet(R.violated_groups, p.violated_groups), R.violated_groups, ...
            p.violated_groups);
if ~isempty(p.identities_ok)
    out = check(out, 'identities_ok', isequal(logical(R.identities_ok), logical(p.identities_ok)), ...
                R.identities_ok, p.identities_ok);
end
out = constraintChecks(out, 'python:constraints', R.constraints, p.constraints, any(strcmp(uns, 'constraints:DC')));
orcs = twb.asCell(c.oracle);
for k = 1:numel(orcs)
    o = orcs{k};
    tag = ['oracle(' o.kind ')'];
    if isfield(o, 'values') && isstruct(o.values)
        names = fieldnames(o.values);
        for j = 1:numel(names)
            n = names{j};
            if any(strcmp(uns, n))
                continue
            end
            if strcmp(o.kind, 'golden')
                t = [o.tolerance.atol o.tolerance.rtol];
            else
                t = tol(C, C.quantities.(n).class);
                t = [t(1) * o.tolerance.atol_scale, o.tolerance.rtol];
            end
            out = number(out, [tag ':' n], R.(n), o.values.(n), t);
        end
    end
    if isfield(o, 'constraints')
        out = constraintChecks(out, [tag ':constraints'], R.constraints, o.constraints, ...
                               any(strcmp(uns, 'constraints:DC')));
    end
    if isfield(o, 'violated_groups')
        out = check(out, [tag ':violated_groups'], sameSet(R.violated_groups, o.violated_groups), ...
                    R.violated_groups, o.violated_groups);
    end
    if isfield(o, 'energy_mode') && ~any(strcmp(uns, 'energy_mode'))
        out = check(out, [tag ':energy_mode'], sameText(R.energy_mode, o.energy_mode), R.energy_mode, o.energy_mode);
    end
    if isfield(o, 'covered')
        out = check(out, [tag ':covered'], isequal(logical(R.evaluable), logical(o.covered)), R.evaluable, o.covered);
    end
end
end

function out = constraintChecks(out, tag, got, want, skipDC)
if isempty(want)
    want = struct();
end
names = twb.uniqueStable([reshape(fieldnames(got), 1, []) reshape(fieldnames(want), 1, [])]);
for k = 1:numel(names)
    n = names{k};
    if skipDC && strncmp(n, 'DC_', 3)
        continue
    end
    a = '';
    b = '';
    if isfield(got, n)
        a = got.(n);
    end
    if isfield(want, n)
        b = want.(n);
    end
    out = check(out, [tag ':' n], strcmp(a, b), a, b);
end
end

function t = tol(C, cls)
t = C.parity_tolerance.classes.(cls);
t = reshape(t, 1, []);
end

function out = number(out, name, a, b, t)
out.checks = out.checks + 1;
if isempty(b)
    b = NaN;
end
if isnan(a) || isnan(b)
    ok = isnan(a) && isnan(b);            % "not defined" only matches "not defined"
else
    ok = abs(a - b) <= t(1) + t(2) * max(abs(a), abs(b));
end
if ~ok
    out.failures{end + 1} = sprintf('%s: target %.17g, reference %.17g (atol %g, rtol %g)', name, a, b, t(1), t(2));
end
end

function out = check(out, name, ok, a, b)
out.checks = out.checks + 1;
if ~ok
    out.failures{end + 1} = sprintf('%s: target %s, reference %s', name, show(a), show(b));
end
end

function s = show(x)
if iscell(x)
    s = ['{' strjoin(cellfun(@show, x, 'UniformOutput', false), ',') '}'];
elseif ischar(x)
    s = x;
elseif islogical(x) && isscalar(x)
    s = mat2str(x);
elseif isempty(x)
    s = '[]';
else
    s = mat2str(x);
end
end

function ok = sameText(a, b)
% '' / [] / null are the same "no value"
if isempty(a) && isempty(b)
    ok = true;
else
    ok = ischar(a) && ischar(b) && strcmp(a, b);
end
end

function ok = sameSet(a, b)
a = twb.asCell(a);
b = twb.asCell(b);
ok = numel(twb.uniqueStable(a)) == numel(twb.uniqueStable(b)) && all(cellfun(@(x) any(strcmp(b, x)), a)) && ...
     all(cellfun(@(x) any(strcmp(a, x)), b));
end

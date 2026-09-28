function stage = runHarness(pkg, outDir)
%RUNHARNESS  Build and simulate one static evaluation harness per (model, scenario) of the evaluable forward
%   cases and compare its outputs with the interpreted twb.staticPoint and with the Python reference values.
%   The harness is a static evaluation block: agreement shows the Simulink execution path computes the exported
%   equations, not a dynamic behaviour.
cs = pkg.cases.forward;
groups = containers.Map();
for k = 1:numel(cs)
    c = cs{k};
    if ~c.python.evaluable || ~isempty(twb.asCell(c.unsupported))
        continue
    end
    key = [c.model '|' jsonencode(c.scenario)];
    if isKey(groups, key)
        groups(key) = [groups(key) k];
    else
        groups(key) = k;
    end
end
keys = groups.keys();
cls = pkg.contract.parity_tolerance.classes;
% y columns: ok psi_d psi_q vd vq vcd vcq Te Tshaft Pac Pcu Pinv Pdc i2
ycls = {'', 'Wb', 'Wb', 'V', 'V', 'V', 'V', 'Nm', 'Nm', 'W', 'W', 'W', 'W', 'A'};
refName = {'', 'psi_d_Wb', 'psi_q_Wb', 'vd_V', 'vq_V', '', '', 'Te_Nm', 'Tshaft_Nm', 'Pac_W', 'Pcu_W', ...
           'Pinv_W', 'Pdc_W', ''};
failures = {};
nPoints = 0;
for g = 1:numel(keys)
    idx = groups(keys{g});
    c0 = cs{idx(1)};
    name = sprintf('twb_static_eval_%d', g);
    info = twb.buildEvaluationHarness(pkg, c0.model, c0.scenario, outDir, name);
    pts = zeros(numel(idx), 3);
    for k = 1:numel(idx)
        pts(k, :) = [k - 1, cs{idx(k)}.point.id_A, cs{idx(k)}.point.iq_A];
    end
    assignin('base', 'twbPoints', pts);
    out = sim(info.name, 'StopTime', num2str(numel(idx) - 1), 'ReturnWorkspaceOutputs', 'on');
    Y = rows14(out.get('twbY'));
    close_system(info.name, 0);
    for k = 1:numel(idx)
        c = cs{idx(k)};
        yi = twb.staticPoint(info.P, c.point.id_A, c.point.iq_A);
        ys = Y(k, :);
        nPoints = nPoints + 1;
        for j = 2:numel(ycls)
            t = reshape(cls.(ycls{j}), 1, []);
            if ~near(ys(j), yi(j), t)
                failures{end + 1} = sprintf('%s col %d: harness %.17g, interpreted %.17g', c.case_id, j, ys(j), yi(j)); %#ok<AGROW>
            end
            if ~isempty(refName{j})
                r = c.python.(refName{j});
                if isempty(r)
                    r = NaN;
                end
                if ~near(ys(j), r, t)
                    failures{end + 1} = sprintf('%s %s: harness %.17g, Python %.17g', c.case_id, refName{j}, ys(j), r); %#ok<AGROW>
                end
            end
        end
    end
end
if isempty(failures)
    status = 'PASS';
else
    status = 'FAIL';
end
stage = struct('status', status, 'detail', sprintf('%d harness models, %d points compared', numel(keys), nPoints), ...
               'failures', {failures}, 'folder', outDir);
end

function Y = rows14(Y)
% the logged 1x14 signal as an N-by-14 array, whatever layout the release logs (N x 14, 1 x 14 x N, 14 x 1 x N)
if isa(Y, 'timeseries')
    Y = Y.Data;
end
Y = double(Y);
if ndims(Y) == 3
    Y = reshape(permute(Y, [3 1 2]), size(Y, 3), []);
end
if size(Y, 2) ~= 14 && size(Y, 1) == 14
    Y = Y';
end
end

function ok = near(a, b, t)
if isnan(a) || isnan(b)
    ok = isnan(a) && isnan(b);
else
    ok = abs(a - b) <= t(1) + t(2) * max(abs(a), abs(b));
end
end

function r = selfChecks(pkg)
%SELFCHECKS  The port's own guards, exercised on modified IN-MEMORY copies (nothing on disk changes):
%   X03 a transposed flux map is refused; X05 a psi_PM temperature law on a flux map (double correction) is
%   refused and a target block's own copper loss is reported as double counting; a changed convention is
%   refused; X09 dictionary conflicts get their statuses (a matching name alone is never a mapping).
r.checks = {};
M = pkg.models.VF_D2_MAP;
planes = twb.asCell(M.motor.flux.planes);
T = M;
pl = planes{1};
pl.psi_d_Wb = pl.psi_d_Wb';
pl.psi_q_Wb = pl.psi_q_Wb';
pl.valid = pl.valid';
T.motor.flux.planes = pl;
r = expectError(r, 'X03 transposed map refused', @() twb.checkModel(T), 'twb:shape');
T = M;
T.motor.flux.psi_temperature = struct('coeff_per_K', -0.0012, 'valid_C', [-40; 180], 'basis', 'test');
r = expectError(r, 'X05 double temperature correction refused', @() twb.checkModel(T), 'twb:doubleTemperature');
C = pkg.contract;
C.conventions.park = 'power_invariant';
r = expectError(r, 'contract with another Park convention refused', @() twb.checkContract(C), 'twb:convention');
c = twb.lossOwnershipConflicts(pkg.models.PRODUCT, struct('copper_fundamental', true, 'harmonic_pwm', true));
r = expect(r, 'X05 target copper loss reported as double counting', numel(c) == 1 && ...
           ~isempty(strfind(c{1}, 'copper_fundamental')), sprintf('%d conflict(s)', numel(c)));
A = twb.readJson(fullfile(pkg.dir, 'architecture', 'candidates.json'));
items = twb.asCell(A.data_items);
it = items(1:4);
existing = {struct('name', it{1}.default_name, 'value', it{1}.value, 'unit', it{1}.unit), ...
            struct('name', it{2}.default_name, 'value', it{2}.value, 'unit', 'mOhm'), ...
            struct('name', 'Company_Name_3', 'value', it{3}.value, 'unit', it{3}.unit), ...
            struct('name', it{4}.default_name, 'value', it{4}.value * 2, 'unit', it{4}.unit)};
profile = struct('naming', struct('data_item_prefix', ''), 'id_map', ...
                 {{struct('id', it{3}.id, 'target', 'Company_Name_3')}});
res = twb.compareDataItems(it, existing, profile);
got = {res.status};
want = {'NAME_MATCH_ONLY', 'CONFLICT_UNIT', 'MAPPED_SAME', 'CONFLICT_VALUE'};
r = expect(r, 'X09 dictionary statuses (name match is not a mapping)', isequal(got, want), strjoin(got, ','));
dup = struct('naming', struct('data_item_prefix', ''), 'id_map', ...
             {{struct('id', it{1}.id, 'target', 'Same'), struct('id', it{2}.id, 'target', 'Same')}});
res = twb.compareDataItems(it(1:2), {}, dup);
r = expect(r, 'X09 two IDs mapped to one target', strcmp(res(2).status, 'CONFLICT_DUPLICATE_TARGET'), res(2).status);
ok = cellfun(@(x) x.pass, r.checks);
r.status = 'PASS';
if ~all(ok)
    r.status = 'FAIL';
end
r.detail = sprintf('%d of %d guard checks pass', sum(ok), numel(ok));
end

function r = expectError(r, name, f, id)
got = 'no error';
try
    f();
catch err
    got = err.identifier;
end
r = expect(r, name, strcmp(got, id), got);
end

function r = expect(r, name, pass, got)
r.checks{end + 1} = struct('name', name, 'pass', logical(pass), 'got', got);
end

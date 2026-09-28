function rep = runParity(pkg)
%RUNPARITY  Every case of the package through the native evaluator, compared with layers 2 and 1.
%   An exception inside one case is recorded as ERROR (an execution failure, kept apart from a semantic FAIL)
%   and the run continues.  rep.status: PASS only when no case FAILs or ERRORs; NOT_SUPPORTED cases are counted
%   separately and never make a PASS.
C = pkg.contract;
rep.cases = {};
counts = struct('PASS', 0, 'FAIL', 0, 'ERROR', 0, 'NOT_SUPPORTED', 0);
sets = {'forward', 'flux_lookup', 'requirement_witness'};
for s = 1:numel(sets)
    cs = pkg.cases.(sets{s});
    for k = 1:numel(cs)
        c = cs{k};
        try
            M = pkg.models.(c.model);
            switch c.kind
                case 'forward'
                    R = twb.evaluatePoint(M, c.scenario, C, c.point.id_A, c.point.iq_A);
                case 'flux_lookup'
                    R = twb.lookupPoint(M, c.magnet_temp_C, C, c.point.id_A, c.point.iq_A);
                case 'requirement_witness'
                    if isempty(c.witness) || isempty(c.python)
                        rec = struct('case_id', c.case_id, 'kind', c.kind, 'status', 'NOT_SUPPORTED', ...
                                     'failures', {{}}, 'checks', 0, 'native', struct(), ...
                                     'detail', 'no witness point: the claim rests on layer-2 exclusion evidence');
                        rep.cases{end + 1} = rec;
                        counts.NOT_SUPPORTED = counts.NOT_SUPPORTED + 1;
                        continue
                    end
                    R = twb.checkWitness(M, c.scenario, C, c.witness, c.request);
                otherwise
                    error('twb:case', 'unknown case kind %s', c.kind);
            end
            cmp = twb.compareCase(c, R, C);
            rec = struct('case_id', c.case_id, 'kind', c.kind, 'status', cmp.status, 'failures', {cmp.failures}, ...
                         'checks', cmp.checks, 'native', R, 'detail', '');
            if ~isempty(cmp.not_supported)
                rec.detail = ['not supported natively: ' strjoin(cmp.not_supported, ', ')];
            end
        catch err
            rec = struct('case_id', c.case_id, 'kind', c.kind, 'status', 'ERROR', 'failures', {{err.message}}, ...
                         'checks', 0, 'native', struct(), 'detail', err.identifier);
        end
        rep.cases{end + 1} = rec;
        counts.(rec.status) = counts.(rec.status) + 1;
    end
end
rep.summary = counts;
rep.summary.total = numel(rep.cases);
if counts.FAIL == 0 && counts.ERROR == 0 && counts.PASS > 0
    rep.status = 'PASS';
else
    rep.status = 'FAIL';
end
end

function checkModel(M)
%CHECKMODEL  Structural and semantic checks of one exported model before anything is evaluated.
%   * conventions equal to the ones implemented (twb.checkContract);
%   * flux-map arrays of size [numel(id_axis) numel(iq_axis)] (row = id): a transposed or reshaped array is
%     refused, never "fixed" by transposing; axes strictly increasing; finite values at every valid node;
%   * a flux map takes its temperature dependence from its planes: a psi_PM temperature law on top would
%     correct twice (refused);
%   * supported families, modulation and voltage-error models only.
if ~isfield(M, 'schema') || ~strcmp(M.schema, 'twb-mathworks-model/1')
    error('twb:schema', 'unknown model schema');
end
twb.checkContract(struct('schema', 'twb-mathworks-contract/1', 'conventions', M.conventions));
f = M.motor.flux;
switch f.kind
    case 'constant_dq'
        if ~(f.Ld_H > 0 && f.Lq_H > 0 && f.psi_pm_Wb >= 0)
            error('twb:model', 'model %s: Ld, Lq > 0 and psi_PM >= 0 are required', M.model_key);
        end
    case 'flux_map'
        if ~isempty(f.psi_temperature)
            error('twb:doubleTemperature', ['model %s: a flux map takes its temperature dependence from its ' ...
                  'planes; a psi_PM temperature law on top would correct twice'], M.model_key);
        end
        planes = twb.asCell(f.planes);
        for k = 1:numel(planes)
            P = planes{k};
            nd = numel(P.id_axis_A);
            nq = numel(P.iq_axis_A);
            if ~isequal(size(P.psi_d_Wb), [nd nq]) || ~isequal(size(P.psi_q_Wb), [nd nq]) || ...
                    ~isequal(size(P.valid), [nd nq])
                error('twb:shape', ['model %s plane %d: arrays must be [numel(id_axis) numel(iq_axis)] = ' ...
                      '[%d %d] (row = id axis); a transposition is never guessed'], M.model_key, k, nd, nq);
            end
            if any(diff(P.id_axis_A) <= 0) || any(diff(P.iq_axis_A) <= 0)
                error('twb:axis', 'model %s plane %d: axes must be strictly increasing', M.model_key, k);
            end
            v = logical(P.valid);
            if any(~isfinite(P.psi_d_Wb(v))) || any(~isfinite(P.psi_q_Wb(v)))
                error('twb:model', 'model %s plane %d: non-finite flux at a valid node', M.model_key, k);
            end
        end
    otherwise
        error('twb:family', 'model %s: unsupported family %s', M.model_key, f.kind);
end
if ~strcmp(M.inverter.voltage.modulation, 'linear_svpwm')
    error('twb:model', 'model %s: only linear SVPWM is part of this contract', M.model_key);
end
if ~any(strcmp(M.inverter.voltage.voltage_error, {'ideal', 'resistive'}))
    error('twb:model', 'model %s: unknown voltage error model %s', M.model_key, M.inverter.voltage.voltage_error);
end
end

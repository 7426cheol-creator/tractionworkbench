function [plane, reason, message] = selectPlane(flux, T, tolC)
%SELECTPLANE  The flux-map plane for magnet temperature T (Python FluxMapModel.plane_for).
%   * T not stated ([]): the single plane of a one-plane map; several planes -> MISSING_INPUT;
%   * a plane within tolC of T;
%   * declared linear temperature interpolation strictly between the lowest and highest plane: the blend
%     (1-w)*lo + w*hi on the intersection of the two validity masks;
%   * otherwise OUTSIDE_MODEL_DOMAIN - planes are never extrapolated in temperature.
%   The returned plane carries cellValid (a cell is valid when its four nodes are valid).
planes = twb.asCell(flux.planes);
plane = [];
reason = '';
message = '';
if isempty(T)
    if numel(planes) == 1
        plane = withCells(planes{1});
    else
        reason = 'MISSING_INPUT';
        message = 'magnet temperature must be stated to select among flux-map planes';
    end
    return
end
temps = [];
for k = 1:numel(planes)
    t = planes{k}.magnet_temp_C;
    if ~isempty(t)
        if abs(t - T) <= tolC
            plane = withCells(planes{k});
            return
        end
        temps(end + 1) = t; %#ok<AGROW>
    end
end
interp = flux.temperature_interpolation;
if ischar(interp) && strcmp(interp, 'linear') && ~isempty(temps) && temps(1) < T && T < temps(end)
    k = find(temps > T, 1);
    lo = planes{k - 1};
    hi = planes{k};
    w = (T - lo.magnet_temp_C) / (hi.magnet_temp_C - lo.magnet_temp_C);
    valid = logical(lo.valid) & logical(hi.valid);
    psd = (1 - w) * lo.psi_d_Wb + w * hi.psi_d_Wb;
    psq = (1 - w) * lo.psi_q_Wb + w * hi.psi_q_Wb;
    psd(~valid) = NaN;
    psq(~valid) = NaN;
    plane = withCells(struct('magnet_temp_C', T, 'id_axis_A', lo.id_axis_A, 'iq_axis_A', lo.iq_axis_A, ...
                             'psi_d_Wb', psd, 'psi_q_Wb', psq, 'valid', valid));
    return
end
reason = 'OUTSIDE_MODEL_DOMAIN';
message = sprintf('no flux-map plane validated at %g degC and interpolation is not permitted', T);
end

function P = withCells(P)
v = logical(P.valid);
P.valid = v;
P.cellValid = v(1:end-1, 1:end-1) & v(2:end, 1:end-1) & v(1:end-1, 2:end) & v(2:end, 2:end);
P.id_axis_A = reshape(P.id_axis_A, 1, []);
P.iq_axis_A = reshape(P.iq_axis_A, 1, []);
end

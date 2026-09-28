function R = evaluatePoint(M, S, C, id, iq, request)
%EVALUATEPOINT  Forward evaluation of the given (id, iq) at scenario S - the point is never moved.
%   R carries every forward quantity (NaN = not defined), the constraint states, the energy mode, the power
%   identities and the evidence gate (accepted / gate_reasons).  Outside the model's covered domain the point is
%   not evaluable: reason OUTSIDE_MODEL_DOMAIN, no numbers (no extrapolation, no clipping).
%   request (optional) - struct('T_request_Nm', T or [], 'band_Nm', [lo hi] or []): the gate then also checks
%   the shaft torque of THIS point against the original request (twb.checkWitness).
if nargin < 6
    request = [];
end
st = C.numerical_settings;
K = twb.resolveModel(M, S, C);
R = emptyResult(C);
R.issues = reasonsOf(K);
R.torque_tolerance_Nm = max(st.torque_residual_abs_Nm, st.torque_residual_rel * K.torqueScale);
if K.evaluable
    y = twb.staticPoint(K.P, id, iq);
    covered = y(1) ~= 0;
else
    covered = false;
end
if ~covered
    R.evaluable = false;
    R.reason = 'OUTSIDE_MODEL_DOMAIN';
    R.gate_reasons = twb.uniqueStable([R.issues {'OUTSIDE_MODEL_DOMAIN'}]);
    R.accepted = false;
    return
end
R.evaluable = true;
if ~isempty(K.issues)
    R.reason = K.issues(1).reason;
end
R.Rs_ohm_used = K.Rs;
R.psi_pm_Wb_used = K.psi;
psd = y(2); psq = y(3); vd = y(4); vq = y(5); vcd = y(6); vcq = y(7);
tem = y(8); tsh = y(9); pac = y(10); pcu = y(11); pinv = y(12); i2 = y(14);
wm = K.omega_m;
R.omega_m_rad_s = wm;
R.omega_e_rad_s = K.omega_e;
R.f_e_Hz = abs(K.omega_e) / (2 * pi);
R.psi_d_Wb = psd;
R.psi_q_Wb = psq;
R.vd_V = vd;
R.vq_V = vq;
R.v_peak_V = hypot(vd, vq);
R.v_LL_rms_V = sqrt(1.5) * R.v_peak_V;
R.v_cmd_peak_V = sqrt(vcd * vcd + vcq * vcq);
R.i_peak_A = sqrt(i2);
R.i_phase_rms_A = R.i_peak_A / sqrt(2);
R.Te_Nm = tem;
R.tau_rot_Nm = K.tauRot;
R.Tshaft_Nm = tsh;
R.Pshaft_W = tsh * wm;
R.Pcu_W = pcu;
R.Prot_W = K.Prot;
R.Pac_W = pac;
if strcmp(K.lossKind, 'quadratic_surrogate')
    R.Pinv_W = pinv;
end
R.Pdc_W = pac + R.Pinv_W;
R.Idc_A = R.Pdc_W / K.Vdc;
R.voltage_budget_V = K.Vb;
R.voltage_ceiling_V = K.V_ceiling;
R.voltage_margin_V = K.Vb - R.v_cmd_peak_V;
if ~isempty(K.fsw) && K.fsw > 0 && R.f_e_Hz > 0
    R.pwm_ratio = K.fsw / R.f_e_Hz;
end
% power identities (the numerical closure of the equations, not a physics check)
res = [pac - (tem * wm + pcu), pac - (R.Pshaft_W + pcu + K.Prot), R.Pdc_W - (pac + R.Pinv_W)];
scale = max(abs([pac, pcu, zeroIfNaN(R.Pdc_W), zeroIfNaN(R.Pshaft_W), tem * wm]));
tolId = max(st.power_identity_abs_W, st.power_identity_rel * scale);
res = res(~isnan(res));
R.identities_ok = all(abs(res) <= tolId);
[R.energy_mode, R.efficiency] = energyMode(R.Pshaft_W, R.Pdc_W, wm, st);
[cons, R.constraints] = constraintsOf(K, st, id, iq, R);
R.violated_groups = {};
for k = 1:numel(cons)
    if strcmp(cons(k).state, 'VIOLATED') && ~any(strcmp(R.violated_groups, cons(k).group))
        R.violated_groups{end + 1} = cons(k).group;
    end
end
[R.gate_reasons, R.torque_residual_Nm] = gate(K, st, R, cons, request);
R.accepted = isempty(R.gate_reasons);
end

function R = emptyResult(C)
q = fieldnames(C.quantities);
R = struct();
for k = 1:numel(q)
    R.(q{k}) = NaN;
end
R.evaluable = false;
R.reason = '';
R.issues = {};
R.accepted = false;
R.gate_reasons = {};
R.constraints = struct();
R.violated_groups = {};
R.energy_mode = '';
R.identities_ok = false;
R.torque_residual_Nm = NaN;
R.torque_tolerance_Nm = NaN;
end

function r = reasonsOf(K)
r = cell(1, numel(K.issues));
for k = 1:numel(K.issues)
    r{k} = K.issues(k).reason;
end
end

function x = zeroIfNaN(x)
if isnan(x)
    x = 0;
end
end

function [mode, eta] = energyMode(ps, pdc, wm, st)
% sign convention: electrical -> mechanical positive; efficiency is never clamped
tol = st.power_zero_tol_W;
eta = NaN;
if isnan(ps) || isnan(pdc)
    mode = 'UNDETERMINED';
elseif abs(wm) <= st.speed_zero_tol_rad_s
    mode = 'STANDSTILL';
elseif abs(ps) <= tol
    mode = 'ZERO_SHAFT_POWER';
elseif ps > tol
    if pdc <= tol
        mode = 'ACCOUNTING_INCONSISTENCY';
    else
        eta = ps / pdc;
        if 0 <= eta && eta <= 1
            mode = 'MOTORING';
        else
            mode = 'ACCOUNTING_INCONSISTENCY';
        end
    end
elseif pdc < -tol
    eta = abs(pdc) / abs(ps);
    if 0 <= eta && eta <= 1
        mode = 'REGENERATING';
    else
        mode = 'ACCOUNTING_INCONSISTENCY';
    end
else
    mode = 'BRAKING_WITHOUT_NET_DC_RECOVERY';
end
end

function [cons, states] = constraintsOf(K, st, id, iq, R)
% slack = limit - demand (upper) / demand - limit (lower); |slack| <= tol is ACTIVE (on the boundary, a pass)
rel = st.constraint_rel_tol;
d = K.domain;
spec = { ...
    'VOLTAGE', 'VOLTAGE', 'upper', K.Vb, R.v_cmd_peak_V, st.voltage_abs_tol_V; ...
    'CURRENT', 'CURRENT', 'upper', K.Imax, R.i_peak_A, st.current_abs_tol_A; ...
    'ID_MIN', 'DOMAIN', 'lower', d.id_A(1), id, st.current_abs_tol_A; ...
    'ID_MAX', 'DOMAIN', 'upper', d.id_A(2), id, st.current_abs_tol_A; ...
    'IQ_MIN', 'DOMAIN', 'lower', d.iq_A(1), iq, st.current_abs_tol_A; ...
    'IQ_MAX', 'DOMAIN', 'upper', d.iq_A(2), iq, st.current_abs_tol_A; ...
    'SPEED_MIN', 'DOMAIN', 'lower', d.speed_rpm(1), K.speed_rpm, st.speed_abs_tol_rpm; ...
    'SPEED_MAX', 'DOMAIN', 'upper', d.speed_rpm(2), K.speed_rpm, st.speed_abs_tol_rpm};
L = K.limits;
dc = { ...
    'DC_DISCHARGE_POWER', 'DISCHARGE_SOURCE', 'upper', 'discharge_power_max_W', 1, R.Pdc_W, st.power_abs_tol_W; ...
    'DC_CHARGE_POWER', 'CHARGE_SOURCE', 'lower', 'charge_power_max_W', -1, R.Pdc_W, st.power_abs_tol_W; ...
    'DC_DISCHARGE_CURRENT', 'DISCHARGE_SOURCE', 'upper', 'discharge_current_max_A', 1, R.Idc_A, st.current_abs_tol_A; ...
    'DC_CHARGE_CURRENT', 'CHARGE_SOURCE', 'lower', 'charge_current_max_A', -1, R.Idc_A, st.current_abs_tol_A};
for k = 1:size(dc, 1)
    lim = L.(dc{k, 4});
    if strcmp(lim.state, 'finite')           % not declared / declared unlimited: no finite constraint
        spec(end + 1, :) = {dc{k, 1}, dc{k, 2}, dc{k, 3}, dc{k, 5} * lim.value, dc{k, 6}, dc{k, 7}}; %#ok<AGROW>
    end
end
cons = struct('name', {}, 'group', {}, 'state', {});
states = struct();
for k = 1:size(spec, 1)
    limit = spec{k, 4};
    demand = spec{k, 5};
    tol = max(spec{k, 6}, rel * abs(limit));
    if strcmp(spec{k, 3}, 'upper')
        slack = limit - demand;
    else
        slack = demand - limit;
    end
    if ~isfinite(slack)
        s = 'NOT_EVALUATED';
    elseif slack < -tol
        s = 'VIOLATED';
    elseif slack <= tol
        s = 'ACTIVE';
    else
        s = 'SATISFIED';
    end
    cons(end + 1) = struct('name', spec{k, 1}, 'group', spec{k, 2}, 'state', s); %#ok<AGROW>
    states.(spec{k, 1}) = s;
end
end

function [reasons, residual] = gate(K, st, R, cons, request)
% the reference's check_witness with every constraint group and the DC side required
reasons = reasonsOf(K);
residual = NaN;
if ~isempty(request) && (~isempty(request.T_request_Nm) || ~isempty(request.band_Nm))
    if isnan(R.Tshaft_Nm)
        reasons{end + 1} = 'MISSING_INPUT';
    else
        t = R.Tshaft_Nm;
        if isempty(request.band_Nm)
            residual = t - request.T_request_Nm;
        else
            lo = request.band_Nm(1);
            hi = request.band_Nm(2);
            if lo <= t && t <= hi
                residual = 0;
            elseif t > hi
                residual = t - hi;
            else
                residual = t - lo;
            end
        end
        if abs(residual) > R.torque_tolerance_Nm
            reasons{end + 1} = 'NUMERICAL_UNRESOLVED';
        end
    end
end
for k = 1:numel(cons)
    if strcmp(cons(k).state, 'NOT_EVALUATED')
        reasons{end + 1} = 'MISSING_INPUT'; %#ok<AGROW>
    elseif strcmp(cons(k).state, 'VIOLATED')
        reasons{end + 1} = 'CONSTRAINT_VIOLATION'; %#ok<AGROW>
    end
end
if isnan(R.Pdc_W)
    reasons{end + 1} = 'MISSING_INPUT';
else
    L = K.limits;
    tol = st.power_zero_tol_W;
    if R.Pdc_W > tol
        keys = {'discharge_power_max_W', 'discharge_current_max_A'};
    elseif R.Pdc_W < -tol
        keys = {'charge_power_max_W', 'charge_current_max_A'};
    else
        keys = {};
    end
    for k = 1:numel(keys)
        if strcmp(L.(keys{k}).state, 'not_declared')      % a missing limit is not an unlimited one
            reasons{end + 1} = 'MISSING_INPUT'; %#ok<AGROW>
        end
    end
end
if ~R.identities_ok
    reasons{end + 1} = 'NUMERICAL_UNRESOLVED';
end
tol = st.power_zero_tol_W;
if R.Pcu_W < -tol || R.Pinv_W < -tol || R.Prot_W < -tol || strcmp(R.energy_mode, 'ACCOUNTING_INCONSISTENCY')
    reasons{end + 1} = 'OUTSIDE_MODEL_DOMAIN';
end
reasons = twb.uniqueStable(reasons);
end

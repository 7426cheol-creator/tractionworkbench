function info = buildEvaluationHarness(pkg, modelKey, scenario, outDir, name)
%BUILDEVALUATIONHARNESS  A Simulink STATIC EVALUATION harness for one exported model at one scenario.
%   One MATLAB Function block evaluates twb.staticPoint (the same code as the interpreted evaluator) for the
%   model resolved at the scenario; a From Workspace block feeds the (id, iq) points, one per sample (discrete,
%   sample time 1 - "time" is only the point index).  This is not a dynamic plant: there are no states, no
%   initial conditions and no controller.  The model is created in outDir only; an existing file is never
%   overwritten and no other model is opened or modified.
%   Requires Simulink.  Written against the documented Simulink / Stateflow API; see results for the run status.
M = pkg.models.(modelKey);
K = twb.resolveModel(M, scenario, pkg.contract);
if ~K.evaluable
    error('twb:harness', 'model %s is not evaluable at this scenario', modelKey);
end
if nargin < 5 || isempty(name)
    name = 'twb_static_eval';
end
if exist(outDir, 'dir') ~= 7
    mkdir(outDir);
end
file = fullfile(outDir, [name '.slx']);
if exist(file, 'file') == 2
    error('twb:harness', '%s exists: the generator never overwrites a model (choose another folder)', file);
end
if bdIsLoaded(name)
    error('twb:harness', 'a model named %s is already loaded', name);
end
new_system(name);
add_block('simulink/Sources/From Workspace', [name '/Points'], 'VariableName', 'twbPoints', ...
          'Interpolate', 'off', 'SampleTime', '1', 'OutputAfterFinalValue', 'Holding final value');
add_block('simulink/User-Defined Functions/MATLAB Function', [name '/StaticEval']);
add_block('simulink/Sinks/To Workspace', [name '/Out'], 'VariableName', 'twbY', 'SaveFormat', 'Timeseries', ...
          'SampleTime', '1');
chart = find(sfroot, '-isa', 'Stateflow.EMChart', 'Path', [name '/StaticEval']);
chart.Script = sprintf(['function y = StaticEval(u, P)\n%%#codegen\n' ...
                        '%% static evaluation of the exported model at one scenario (not a dynamic plant)\n' ...
                        'y = twb.staticPoint(P, u(1), u(2));\n']);
par = find(chart, '-isa', 'Stateflow.Data', 'Name', 'P');
par.Scope = 'Parameter';
set_param(name, 'SolverType', 'Fixed-step', 'Solver', 'FixedStepDiscrete', 'FixedStep', '1');
assignin(get_param(name, 'ModelWorkspace'), 'P', K.P);
add_line(name, 'Points/1', 'StaticEval/1');
add_line(name, 'StaticEval/1', 'Out/1');
save_system(name, file);
info = struct('file', file, 'name', name, 'model', modelKey, 'P', K.P);
end

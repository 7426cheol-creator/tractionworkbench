function env = preflight()
%PREFLIGHT  What this installation can run - reported, never assumed.
%   Products are detected from their installation folders (ver); a licence or API problem found later is an
%   execution failure of that stage, not a pass.  Under GNU Octave the MATLAB-language evaluator runs, and every
%   Simulink / System Composer / dictionary stage stays NOT_RUN.
env.schema = 'twb-mathworks-preflight/1';
env.created = datestr(now, 31);
isOctave = exist('OCTAVE_VERSION', 'builtin') ~= 0;
if isOctave
    env.product = 'GNU Octave';
    env.version = OCTAVE_VERSION;
    env.release = '';
    env.note = ['MATLAB-language proxy runner: the native evaluator and the parity check run; MATLAB, Simulink, ' ...
                'System Composer and dictionary stages are NOT_RUN here'];
else
    env.product = 'MATLAB';
    env.version = version;
    env.release = version('-release');
    env.note = '';
end
env.computer = computer;
env.sha256 = 'none';
if isOctave
    env.sha256 = 'octave hash()';
elseif usejava('jvm')
    env.sha256 = 'java.security.MessageDigest';
end
env.toolboxes = struct();
names = {'simulink', 'Simulink'; 'systemcomposer', 'System Composer'; 'simscape', 'Simscape'; ...
         'sltest', 'Simulink Test'; 'slrequirements', 'Requirements Toolbox'; 'coder', 'MATLAB Coder'};
for k = 1:size(names, 1)
    avail = false;
    reason = 'GNU Octave: not available';
    if ~isOctave
        try
            avail = ~isempty(ver(names{k, 1}));
            reason = '';
            if ~avail
                reason = [names{k, 2} ' not installed'];
            end
        catch err
            reason = err.message;
        end
    end
    env.toolboxes.(names{k, 1}) = struct('name', names{k, 2}, 'available', avail, 'reason', reason);
end
end

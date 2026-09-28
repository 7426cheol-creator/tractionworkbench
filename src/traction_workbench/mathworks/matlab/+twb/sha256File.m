function hex = sha256File(path)
%SHA256FILE  SHA-256 of a file's bytes as lowercase hex.
%   Returns '' when no SHA-256 provider exists (MATLAB without its Java runtime); callers report that as
%   NOT_RUN - an unchecked hash is never counted as a passed one.
fid = fopen(path, 'r');
if fid < 0
    error('twb:io', 'cannot open %s', path);
end
bytes = fread(fid, Inf, '*uint8');
fclose(fid);
hex = '';
if exist('OCTAVE_VERSION', 'builtin') ~= 0
    hex = lower(hash('sha256', char(bytes')));
elseif usejava('jvm')
    md = java.security.MessageDigest.getInstance('SHA-256');
    if ~isempty(bytes)
        md.update(typecast(bytes, 'int8'));
    end
    d = typecast(md.digest(), 'uint8');
    hex = lower(reshape(dec2hex(d, 2)', 1, []));
end
end

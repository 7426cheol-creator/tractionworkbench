function s = readJson(path)
%READJSON  Decode one JSON file of the transfer package.
%   The exporter writes ASCII only (non-ASCII text is \u-escaped), so the bytes decode identically in MATLAB
%   and GNU Octave whatever the platform's default character encoding is.
fid = fopen(path, 'r');
if fid < 0
    error('twb:io', 'cannot open %s', path);
end
bytes = fread(fid, Inf, '*uint8')';
fclose(fid);
if numel(bytes) >= 3 && isequal(bytes(1:3), uint8([239 187 191]))
    bytes = bytes(4:end);
end
s = jsondecode(char(bytes));
end

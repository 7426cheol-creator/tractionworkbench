function writeJson(path, s)
%WRITEJSON  Encode s as JSON (NaN -> null: "not defined", never zero) and write it as ASCII.
txt = jsonencode(s);
txt(double(txt) > 127) = '?';
fid = fopen(path, 'w');
if fid < 0
    error('twb:io', 'cannot write %s', path);
end
fwrite(fid, txt, 'char');
fclose(fid);
end

function c = asCell(x)
%ASCELL  A JSON array as a 1-by-n cell array (jsondecode returns a struct array, a cell array or a numeric array
%   depending on the elements; empty arrays decode to []).
if isempty(x)
    c = {};
elseif iscell(x)
    c = reshape(x, 1, []);
else
    c = num2cell(reshape(x, 1, []));
end
end

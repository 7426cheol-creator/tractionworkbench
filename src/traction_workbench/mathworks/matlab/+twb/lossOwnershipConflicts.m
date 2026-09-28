function c = lossOwnershipConflicts(M, target)
%LOSSOWNERSHIPCONFLICTS  Loss terms counted twice when a target block brings its own losses.
%   M      - an exported model (its loss_ownership list)
%   target - struct of logicals: which loss terms the target block computes itself, e.g.
%            struct('copper_fundamental', true, 'iron', true, 'inverter', false)
%   A term the exported model already owns and the target computes as well is a DOUBLE_COUNT; the exported
%   closure or the block's loss must be switched off before the two are combined.
c = {};
own = twb.asCell(M.loss_ownership);
for k = 1:numel(own)
    o = own{k};
    owned = ~strncmp(o.owner, 'not ', 4);
    if owned && isfield(target, o.term) && target.(o.term)
        c{end + 1} = sprintf('DOUBLE_COUNT %s: already in the exported model (%s)', o.term, o.owner); %#ok<AGROW>
    end
end
end

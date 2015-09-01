import collections

def validate_abbreviations(metagraph):
    """Check that abbreviations are unambigious"""
    valid = True
    metanodes = set(metagraph.get_nodes())
    metaedges = set(metagraph.get_edges(exclude_inverts=False))

    # Duplicated metanode and metaedge kinds
    metanode_kinds = {metanode.identifier for metanode in metanodes}
    metaedge_kinds = {metaedge.kind for metaedge in metaedges}
    duplicated_kinds = metanode_kinds & metaedge_kinds
    if duplicated_kinds:
        print('Duplicated kinds between metanodes and metaedges:', duplicated_kinds)
        valid = False

    # Check that metanodes do not have any duplicated abbreviations
    kind_to_abbrev = metagraph.kind_to_abbrev
    metanode_kind_to_abbrev = {k: v for k, v in kind_to_abbrev.items() if k in metanode_kinds}
    duplicated_metanode_abbrevs = get_duplicates(metanode_kind_to_abbrev.values())
    if duplicated_metanode_abbrevs:
        print('Duplicated metanode abbrevs:', duplicated_metanode_abbrevs)
        valid = False

    # Check capitalizations
    # metanode abbreviations should be uppercase
    for metanode in metanodes:
        abbrev = metanode.abbrev
        if not abbrev.isupper():
            print('lowercase metanode abbreviation:', abbrev)
            valid = False
    # metaedge abbreviations should be lowercase
    for metaedge in metaedges:
        abbrev = metaedge.kind_abbrev
        if not abbrev.islower():
            print('uppercase metaedge abbreviation:', abbrev)
            valid = False

    # Check that metaedges are not ambigious
    metaedge_abbrevs = [metaedge.get_abbrev() for metaedge in metaedges]
    duplicated_meataedge_abbrevs = get_duplicates(metaedge_abbrevs)
    if duplicated_meataedge_abbrevs:
        print('Duplicated metaedge abbreviations:', duplicated_meataedge_abbrevs)
        valid = False

    return valid

def get_duplicates(iterable):
    """Return a set of the elements which appear multiple times in iterable."""
    counter = collections.Counter(iterable)
    return {key for key, count in counter.items() if count > 1}

def find_abbrevs(kinds):
    """
    For a list of strings (kinds), find the shortest unique abbreviation.
    All returned abbrevs are lowercase.
    """
    kind_to_abbrev = {kind: kind[0].lower() for kind in kinds}
    duplicates = get_duplicates(kind_to_abbrev.values())
    while duplicates:
        for kind, abbrev in list(kind_to_abbrev.items()):
            if abbrev in duplicates and len(abbrev) < len(kind):
                abbrev += kind[len(abbrev)].lower()
                kind_to_abbrev[kind] = abbrev
        duplicates = get_duplicates(kind_to_abbrev.values())
    return kind_to_abbrev

def create_abbreviations(metagraph):
    """Creates abbreviations for node and edge kinds."""
    kind_to_abbrev = find_abbrevs(metagraph.node_dict.keys())
    kind_to_abbrev = {kind: abbrev.upper()
                      for kind, abbrev in kind_to_abbrev.items()}

    edge_set_to_keys = dict()
    for edge in list(metagraph.edge_dict.keys()):
        key = frozenset(list(map(str.lower, edge[:2])))
        value = edge[2]
        edge_set_to_keys.setdefault(key, list()).append(value)

    for edge_set, keys in list(edge_set_to_keys.items()):
        key_to_abbrev = find_abbrevs(keys)
        for key, abbrev in list(key_to_abbrev.items()):
            previous_abbrev = kind_to_abbrev.get(key)
            if previous_abbrev and len(abbrev) <= len(previous_abbrev):
                continue
            kind_to_abbrev[key] = abbrev

    return kind_to_abbrev

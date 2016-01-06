import functools
import itertools
import textwrap
import random
import re

import py2neo
import py2neo.packages.httpstream
import pandas

import hetio.hetnet
from hetio.abbreviation import arrange_metaege

# Avoid SocketError
py2neo.packages.httpstream.http.socket_timeout = 1e8

def export_neo4j(graph, uri, node_queue=100, edge_queue=100):
    """Export hetnet to neo4j"""
    db_graph = py2neo.Graph(uri)

    # Delete all existing nodes
    db_graph.delete_all()

    # Create uniqueness constrains and indexes
    for metanode in graph.metagraph.get_nodes():
        label = as_label(metanode)
        if 'identifier' not in db_graph.schema.get_uniqueness_constraints(label):
            db_graph.schema.create_uniqueness_constraint(label, 'identifier')
        if 'name' not in db_graph.schema.get_indexes(label):
            db_graph.schema.create_index(label, 'name')

    # Create nodes
    creator = Creator(db_graph, node_queue)
    for node in graph.get_nodes():
        label = as_label(node.metanode)
        data = sanitize_data(node.data)
        neo_node = py2neo.Node(label, identifier=node.identifier, name=node.name, **data)
        creator.append(neo_node)
    creator.create()

    # Create edges
    creator = Creator(db_graph, edge_queue)
    for edge in graph.get_edges(exclude_inverts=True):
        metaedge = edge.metaedge
        rel_type = as_type(metaedge)
        source_label = as_label(metaedge.source)
        target_label = as_label(metaedge.target)
        source = db_graph.find_one(source_label, 'identifier', edge.source.identifier)
        target = db_graph.find_one(target_label, 'identifier', edge.target.identifier)
        data = sanitize_data(edge.data)
        neo_rel = py2neo.Relationship(source, rel_type, target, **data)
        creator.append(neo_rel)
    creator.create()

    return db_graph


class Creator(list):
    """Batch creation of py2neo objects"""

    def __init__(self, db_graph, max_queue_size=25):
        super().__init__()
        self.db_graph = db_graph
        self.max_queue_size = max_queue_size
        self.n_created = 0

    def append(self, x):
        super().append(x)
        if len(self) >= self.max_queue_size:
            self.create()

    def create(self):
        if not self:
            return
        self.db_graph.create(*self)
        self.n_created += len(self)
        #print('{} nodes created\r'.format(self.n_created), end='')
        del self[:]

@functools.lru_cache()
def as_label(metanode):
    """Convert metanode to a label-formatted str"""
    label = str(metanode)
    label = label.title()
    label = label.replace(' ', '')
    return label

@functools.lru_cache()
def as_type(metaedge):
    """Convert metaedge to a rel_type-formatted str"""
    assert isinstance(metaedge, hetio.hetnet.MetaEdge)
    rel_type = str(metaedge.kind)
    rel_type = rel_type.upper()
    rel_type = rel_type.replace(' ', '_')
    abbrev = metaedge.get_abbrev()
    abbrev = arrange_metaege(abbrev)
    abbrev = re.sub('[<>]', '', abbrev)
    return '{}_{}'.format(rel_type, abbrev)

def sanitize_data(data):
    """Create neo4j safe properties"""
    sanitized = dict()
    for k, v in data.items():
        if isinstance(v, list):
            sanitized[k] = v
            continue
        if pandas.isnull(v):
            continue
        if v is None:
            continue
        sanitized[k] = v
    return sanitized

def metapath_to_metarels(metapath):
    return tuple(metaedge_to_metarel(metaedge) for metaedge in metapath)

@functools.lru_cache()
def metaedge_to_metarel(metaedge):
    source, target, kind, direction = metaedge.get_id()
    return as_label(source), as_label(target), as_type(metaedge), direction

def cypher_path(metarels):
    """
    Format a metapath for cypher.
    """
    # Convert metapath to metarels
    if isinstance(metarels, hetio.hetnet.MetaPath):
        metarels = metapath_to_metarels(metarels)

    # Create cypher query
    q = '(n0:{})'.format(metarels[0][0])
    for i, (source_label, target_label, rel_type, direction) in enumerate(metarels):
        kwargs = {
            'i': i + 1,
            'rel_type': rel_type,
            'target_label': ':{}'.format(target_label) if i + 1 == len(metarels) else '',
            'dir0': '<-' if direction == 'backward' else '-',
            'dir1': '->' if direction == 'forward' else '-',
        }
        q += '{dir0}[:{rel_type}]{dir1}(n{i}{target_label})'.format(**kwargs)
    return q

def construct_dwpc_query(metarels, property='name', using=True, unique_nodes=False):
    """
    Create a cypher query for computing the *DWPC* for a type of path.

    Parameters
    ----------
    metarels : a metarels or MetaPath object
        the metapath (path type) to create a query for
    property : str
        which property to use for soure and target node lookup
    using : bool
        whether to add `USING` clauses into the query, which direct neo4j to
        start traversal from both ends of the path and meet in the middle.
    unique_nodes : bool or str
        whether to exclude paths with duplicate nodes. `True` or `nested` uses
        the path-length independent query:
        `ALL (x IN nodes(paths) WHERE size(filter(z IN nodes(paths) WHERE z = x)) = 1)`
        whereas `expanded` uses the combinatorial and path-length dependent form:
        `NOT (n0=n1 OR n0=n2 OR n0=n3 OR n1=n2 OR n1=n3 OR n2=n3)`. Specify
        `labeled` to perform an intelligent version of `expanded` where only
        nodes with the same label are checked for duplicity. To not enforce
        node uniqueness, use `False`.
    """
    # Convert metapath to metarels
    if isinstance(metarels, hetio.hetnet.MetaPath):
        metarels = metapath_to_metarels(metarels)

    # create cypher path query
    metapath_query = cypher_path(metarels)

    # create cypher path degree query
    degree_strs = list()
    for i, (source_label, target_label, rel_type, direction) in enumerate(metarels):
        kwargs = {
            'i0': i,
            'i1': i + 1,
            'source_label': source_label,
            'target_label': target_label,
            'rel_type': rel_type,
            'dir0': '<-' if direction == 'backward' else '-',
            'dir1': '->' if direction == 'forward' else '-',
        }
        degree_strs.append(textwrap.dedent(
            '''\
            size((n{i0}){dir0}[:{rel_type}]{dir1}()),
            size((){dir0}[:{rel_type}]{dir1}(n{i1}))'''
            ).format(**kwargs))
    degree_query = ',\n'.join(degree_strs)

    if using:
        using_query = textwrap.dedent('''\
        USING INDEX n0:{source_label}({property})
        USING INDEX n{length}:{target_label}({property})
        ''').format(
            property = property,
            source_label = metarels[0][0],
            target_label = metarels[-1][1],
            length = len(metarels)
        )
    else:
        using_query = ''

    # Unique node constraint (pevent paths with duplicate nodes)
    if unique_nodes is True or unique_nodes == 'nested':
        unique_nodes_query = '\nAND ALL (x IN nodes(paths) WHERE size(filter(z IN nodes(paths) WHERE z = x)) = 1)'
    elif unique_nodes == 'expanded':
        pairs = itertools.combinations(range(len(metarels) + 1), 2)
        unique_nodes_query = format_expanded_clause(pairs)
    elif unique_nodes == 'labeled':
        labels = [metarel[0] for metarel in metarels]
        labels.append(metarels[-1][1])
        label_to_nodes = dict()
        for i, label in enumerate(labels):
            label_to_nodes.setdefault(label, list()).append(i)
        pairs = list()
        for nodes in label_to_nodes.values():
            pairs.extend(itertools.combinations(nodes, 2))
        unique_nodes_query = format_expanded_clause(pairs)
    else:
        assert unique_nodes is False
        unique_nodes_query = ''

    # combine cypher fragments into a single query and add DWPC logic
    query = textwrap.dedent('''\
        MATCH paths = {metapath_query}
        {using_query}WHERE n0.{property} = {{ source }}
        AND n{length}.{property} = {{ target }}{unique_nodes_query}
        WITH
        [
        {degree_query}
        ] AS degrees, paths
        RETURN
        count(paths) AS PC,
        sum(reduce(pdp = 1.0, d in degrees| pdp * d ^ -{{ w }})) AS DWPC\
        ''').format(
        metapath_query = metapath_query,
        using_query = using_query,
        unique_nodes_query = unique_nodes_query,
        degree_query = degree_query,
        length=len(metarels),
        property=property)

    return query

def format_expanded_clause(pairs):
    """
    Given an iterable of node-index pairs, return a cypher `WHERE` clause
    for excluding paths where a pair of nodes are equal.
    """
    if not pairs:
        return ''
    pair_str = ' OR '.join('n{} = n{}'.format(a, b) for a, b in pairs)
    return '\nAND NOT ({})'.format(pair_str)

def permute_rel_type(uri, rel_type, nswap=None, max_tries=None, nswap_mult=10, max_tries_mult=20, seed=None):
    """
    Permute the specified relationship type in a neo4j graph using the XSwap
    algorithm [1]_.

    Parameters
    ----------
    uri : str
        neo4j server connection information
    rel_type : str
        the relationship type to permute
    nswap : None or int, optional
        the number of successful swaps to perform
    max_tries : None or int, optional
        the maximum number of attempted swaps
    nswap_mult : float or int, optional
        when `nswap_mult is None`, set `nswap` to the total number of
        relationships multiplied by `nswap_mult`
    max_tries_mult : float or int, optional
        when `max_tries is None`, set `max_tries` to the total number of
        relationships multiplied by `max_tries_mult`
    seed : int or None, optional
        value used to initialize the random generation of relationships for
        swapping

    References
    ----------

    .. [1] Sami Hanhijärvi, Gemma C. Garriga, Kai Puolamäki (2009)
       Randomization Techniques for Graphs. SIAM International Conference on
       Data Mining. https://doi.org/10.1137/1.9781611972795.67
    """

    neo = py2neo.Graph(uri)

    # retrieve relationship IDs
    query = textwrap.dedent('''\
    MATCH ()-[r:{rel_type}]->()
    RETURN id(r) AS id\
    '''.format(rel_type=rel_type))
    ids = [row.id for row in neo.cypher.execute(query)]
    nrel = len(ids)
    if nswap is None:
        nswap = round(nrel * nswap_mult)
    if max_tries is None:
        max_tries = round(nrel * max_tries_mult)

    query = textwrap.dedent('''\
    MATCH (u)-[r0:{rel_type}]->(v)
    MATCH (x)-[r1:{rel_type}]->(y)
    WHERE id(r0) = {{ id_0 }}
    AND id(r1) = {{ id_1 }}
    AND u <> x
    AND v <> y
    AND NOT exists((u)-[:{rel_type}]-(y))
    AND NOT exists((x)-[:{rel_type}]-(v))
    CREATE (u)-[nr0:{rel_type}]->(y)
    CREATE (x)-[nr1:{rel_type}]->(v)
    DELETE r0, r1
    RETURN id(nr0) AS id_nr0, id(nr1) AS id_nr1\
    '''.format(rel_type=rel_type))

    swaps = 0
    tries = 0
    random.seed(seed, version=2)
    while swaps < nswap and tries < max_tries:
        index_0, index_1 = random.sample(range(nrel), 2)
        id_0 = ids[index_0]
        id_1 = ids[index_1]

        result = neo.cypher.execute(query, id_0=id_0, id_1=id_1)
        if result:
            ids[index_0] = result.one.id_nr0
            ids[index_1] = result.one.id_nr1
            swaps += 1
        tries += 1

    stats = {
        'swaps': swaps,
        'tries': tries,
    }
    return stats

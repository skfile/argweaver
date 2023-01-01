#
# ARGweaver: Ancestral Recombination Graph sampling
#

# python libs
from collections import defaultdict
from contextlib import closing
import heapq
from itertools import chain
from math import exp, log
import random
import os
import subprocess

# add pre-bundled dependencies to the python path,
# if they are not available already
try:
    import compbio
    import rasmus
    rasmus, compbio  # suppress unused pyflakes warning
except ImportError:
    from . import dep
    dep.load_deps()
    import compbio
    import rasmus

# rasmus combio libs
rasmus, compbio  # suppress unused pyflakes warning
from compbio import arglib, alignlib, fasta
from rasmus import util, treelib

# argweaver libs
from argweaver.sim import make_alignment
from argweaver.sim import make_sites
from argweaver.sim import sample_arg_dsmc
from argweaver.sim import sample_arg_mutations

from argweaver.smc import SMCReader
from argweaver.smc import arg2smc
from argweaver.smc import get_smc_sample_iter
from argweaver.smc import iter_smc_file
from argweaver.smc import iter_smc_trees
from argweaver.smc import iter_subsmc
from argweaver.smc import list_smc_files
from argweaver.smc import read_smc
from argweaver.smc import smc2arg
from argweaver.smc import smc2sprs
from argweaver.smc import read_arg
from argweaver.smc import write_smc

#from argweaverc import calc_joint_prob

# suppress unused pyflakes warning
#calc_joint_prob
make_alignment
make_sites
sample_arg_dsmc
sample_arg_mutations
SMCReader
arg2smc
get_smc_sample_iter
iter_smc_file
iter_smc_trees
iter_subsmc
list_smc_files
read_smc
smc2arg
smc2sprs
read_arg
write_smc


#=============================================================================
# constants

PROGRAM_NAME = "argweaver"
PROGRAM_VERSION_MAJOR = 0
PROGRAM_VERSION_MINOR = 8
PROGRAM_VERSION_RELEASE = 1
PROGRAM_VERSION = (PROGRAM_VERSION_MAJOR,
                   PROGRAM_VERSION_MINOR,
                   PROGRAM_VERSION_RELEASE)

if PROGRAM_VERSION_RELEASE != 0:
    PROGRAM_VERSION_TEXT = "%d.%d.%d" % (PROGRAM_VERSION_MAJOR,
                                         PROGRAM_VERSION_MINOR,
                                         PROGRAM_VERSION_RELEASE)
else:
    PROGRAM_VERSION_TEXT = "%d.%d" % (PROGRAM_VERSION_MAJOR,
                                      PROGRAM_VERSION_MINOR)


#=============================================================================
# discretization


def get_time_point(i, ntimes, maxtime, delta=10):
    """Returns a discretized time point"""
    return (exp(i/float(ntimes) * log(1 + delta * maxtime)) - 1) / delta


def get_time_points(ntimes=30, maxtime=80000, delta=.01):
    """Returns a list of discretized time points"""
    return [get_time_point(i, ntimes-1, maxtime, delta)
            for i in range(ntimes)]


def iter_coal_states(tree, times):
    """Iterates through the coalescent states of a local tree"""

    # NOTE: do not use top time
    ntimes = len(times) - 1
    seen = set()
    time_lookup = dict((t, i) for i, t in enumerate(times))

    for node in tree.preorder():
        if len(node.children) == 1:
            continue
        i = time_lookup[node.age]

        if node.parents:
            parent = node.parents[0]
            while parent and parent not in seen:
                parent = parent.parents[0]

            while i < ntimes and times[i] <= parent.age:
                yield (node.name, i)
                i += 1
        else:
            while i < ntimes:
                yield (node.name, i)
                i += 1

        seen.add(node)


def get_nlineages(tree, times):
    """
    Count the number of lineages at each time step
    """
    if isinstance(tree, arglib.ARG):
        nbranches = [0 for i in range(len(times))]

        for node in tree:
            if node.parents:
                p = times.index(node.parents[0].age)
                n = times.index(node.age)

                for i in range(n, p):
                    nbranches[i] += 1
            else:
                for i in range(times.index(node.age), len(times)):
                    nbranches[i] += 1
        return nbranches

    else:
        ages = treelib.get_tree_ages(tree)
        for node, age in list(ages.items()):
            ages[node] = min(times, key=lambda x: abs(x - age))

        nbranches = [0 for i in range(len(times))]

        for node in tree:
            if node.parent:
                p = times.index(ages[node.parent])
                n = times.index(ages[node])

                for i in range(n, p):
                    nbranches[i] += 1
            else:
                for i in range(times.index(ages[node]), len(times)):
                    nbranches[i] += 1
        return nbranches


def get_nlineages_recomb_coal(tree, times):
    """
    Count the number of lineages at each time point that can coal and recomb
    """

    # TODO: is nrecombs including basal point?  It shouldn't

    nbranches = [0 for i in times]
    nrecombs = [0 for i in times]
    ncoals = [0 for i in times]

    for name, timei in iter_coal_states(tree, times):
        node = tree[name]

        # find parent node
        if node.parents:
            parent = node.parents[0]
            while len(parent.children) == 1:
                parent = parent.parents[0]
        else:
            parent = None

        # count who passes through this time segment
        if not parent or times[timei] < parent.age:
            nbranches[timei] += 1

        # count as recomb and coal point
        nrecombs[timei] += 1
        ncoals[timei] += 1
    nbranches[-1] = 1

    return nbranches, nrecombs, ncoals


def discretize_arg(arg, times2):
    """
    Round node ages to the nearest time point
    times2 has 2n time points; the even indices give the discretized time
    points and the odd indices give the boundaries for rounding; ie
    a time between times2[2i-1] and times2[2i] will have time set to times2[2i]
    """

    for node in arg:
        i, j = util.binsearch(times2, node.age)
        if j is None:
            j = len(times2) - 1
            node.age = times2[ length(times2) - 1 ]
        else:
            assert i is not None
            if i % 2 == 0:
                node.age = times2[i]
            else:
                assert i+1 == j
                node.age = times2[j]

    recombs = [node for node in arg if node.event == "recomb"]
    recombs.sort(key=lambda x: x.pos)

    last = 0
    for node in recombs:
        intpos = int(node.pos)
        if intpos > last:
            node.pos = intpos
        else:
            node.pos = last + 1
        last = node.pos

    # ensure no duplicate recombinations
    seen = set()
    for node in arg:
        if node.event == "recomb":
            assert node.pos not in seen, (node.pos, sorted(seen))
            seen.add(node.pos)


def discretize_arg_recomb(arg):
    """Round recomb node to the nearest integer"""

    recombs = [node for node in arg if node.event == "recomb"]
    recombs.sort(key=lambda x: x.pos)

    last = 0
    for node in recombs:
        intpos = int(node.pos)
        if intpos > last:
            node.pos = intpos
        else:
            node.pos = last + 1
        last = node.pos

    # ensure no duplicate recombinations
    seen = set()
    for node in arg:
        if node.event == "recomb":
            assert node.pos not in seen, (node.pos, sorted(seen))
            seen.add(node.pos)


#=============================================================================
# tree length calculations

def get_treelen(tree, times, use_basal=True):
    """Calculate tree length"""
    treelen = sum(x.get_dist() for x in tree)
    if use_basal:
        rooti = times.index(tree.root.age)
        root_time = times[rooti+1] - times[rooti]
        treelen += root_time
    return treelen


def get_treelen_branch(tree, times, node, time, use_basal=True):
    """Calculate tree length with an extra branch"""

    treelen = sum(x.get_dist() for x in tree)

    blen = time
    treelen2 = treelen + blen
    if node == tree.root.name:
        treelen2 += blen - tree.root.age
        rooti = times.index(time)
        root_time = times[rooti+1] - times[rooti]
    else:
        rooti = times.index(tree.root.age)
        root_time = times[rooti+1] - times[rooti]

    if use_basal:
        treelen2 += root_time

    return treelen2


def get_basal_length(tree, times, node=None, time=None):
    """
    Get basal branch length

    NOTE: 'node' can be None
    """

    if node == tree.root.name:
        rooti = times.index(time)
        root_time = times[rooti+1] - times[rooti]
    else:
        rooti = times.index(tree.root.age)
        root_time = times[rooti+1] - times[rooti]

    return root_time


#=============================================================================
# alignment compression


def is_variant(seqs, pos):
    """Returns True if site 'pos' in align 'seqs' is polymorphic"""
    seqs = list(seqs.values())
    c = seqs[0][pos]
    for i in range(1, len(seqs)):
        if seqs[i][pos] != c:
            return True
    return False


def compress_align_cols(seqs, compress):
    """Compress an alignment 'seqs' by a factor of 'compress'"""

    cols_used = []
    seen_variant = False
    next_block = compress - 1
    for i in range(seqs.alignlen()):
        if i % compress == 0:
            seen_variant = False

        if i == next_block:
            if not seen_variant:
                # choose invariant site
                cols_used.append(i-1)
            seen_variant = False
            next_block += compress

        if is_variant(seqs, i):
            seen_variant = True
            cols_used.append(i)
            next_block += compress

    return cols_used


def compress_align(seqs, compress):
    """Compress an alignment 'seqs' by a factor of 'compress'"""

    cols_used = compress_align_cols(seqs, compress)
    return alignlib.subalign(seqs, cols_used), cols_used


#=============================================================================
# input/output


class WrapStream(object):
    """Wrap file input/output with additional commands."""

    def __init__(self, write_command=None, read_command=None):
        self.write_command = write_command
        self.read_command = read_command

    def set_gzip(self):
        """Use gzip for compression/decompression"""
        self.write_command = ["gzip", "-"]
        self.read_command = ["gzip", "-d", "-c"]

    def set_bgzip(self):
        """Use bgzip for compression/decompression"""
        self.write_command = ["bgzip"]
        self.read_command = ["bgzip", "-d", "-c"]

    def open(self, filename, mode="r"):
        """Open a stream."""
        null = open(os.devnull, 'w')
        if mode == "r":
            if not os.path.exists(filename):
                raise Exception("unknown file '%s'" % filename)
            return subprocess.Popen(self.read_command + [filename],
                                    stdout=subprocess.PIPE,
                                    stderr=null).stdout
        elif mode == "w":
            with closing(open(filename, "w")) as out:
                return subprocess.Popen(self.write_command,
                                        stdout=out,
                                        stdin=subprocess.PIPE,
                                        stderr=null).stdin
        else:
            raise Exception("unknown mode '%s'" % mode)


gzip_stream = WrapStream()
gzip_stream.set_gzip()

bgzip_stream = WrapStream()
bgzip_stream.set_bgzip()


def open_stream(filename, mode="r", compress='gzip'):
    """Open a stream and auto-detect whether file is compressed (*.gz)"""

    # auto-detect compressed filenames
    if isinstance(filename, str) and filename.endswith(".gz"):
        if compress == 'gzip':
            return gzip_stream.open(filename, mode)
        elif compress == 'bgzip':
            return bgzip_stream.open(filename, mode)
        else:
            raise Exception("unknown compression '%s'" % compress)
    return util.open_stream(filename, mode)


#=============================================================================
# sites

class Sites (object):
    """
    Data structure for representing the variant sites in an alignment

    region is 1-indexed and end inclusive
    site positions are also 1-indexed
    """

    def __init__(self, names=None, chrom="chr", region=None):
        if names:
            self.names = names
        else:
            self.names = []

        self.chrom = chrom
        if region:
            self.region = region
        else:
            self.region = [0, 0]

        self.positions = []
        self._cols = {}

    #==================================================================
    # dimensions

    def length(self):
        """Returns overall length of alignment"""
        return self.region[1] - self.region[0] + 1

    def nseqs(self):
        """Returns number of sequences in alignment"""
        return len(self._cols[self.positions[0]])

    def nsites(self):
        """Returns number of sites in alignment"""
        return len(self._cols)

    def append(self, pos, col):
        """Adds a site to alignment with position 'pos' and column 'col'"""
        self.positions.append(pos)
        self._cols[pos] = col

    def get(self, pos, name=None, names=None):
        """Returns column at position 'pos'"""
        col = self._cols[pos]
        if name is not None:
            return col[self.names.index(name)]
        elif names is not None:
            return "".join(col[self.names.index(name)] for name in names)
        return col

    def set(self, pos, col):
        """Sets a site with column 'col' at position 'pos'"""
        self._cols[pos] = col

    def get_seq(self, name, start=None, end=None):
        """Returns a sequence from the alignment"""
        i = self.names.index(name)
        seq = []
        for pos, col in self.iter_region(start, end):
            seq.append(col[i])
        return "".join(seq)

    def get_minor(self, pos):
        """Returns the names of sequences with the minor allele"""
        col = self._cols[pos]
        part1 = []
        part2 = []

        c = col[0]
        for i in range(len(col)):
            if col[i] == c:
                part1.append(self.names[i])
            else:
                part2.append(self.names[i])
        return min([part1, part2], key=len)

    def get_major(self, pos):
        """Returns the names of sequences with the major allele"""
        col = self._cols[pos]
        part1 = []
        part2 = []

        c = col[0]
        for i in range(len(col)):
            if col[i] == c:
                part1.append(self.names[i])
            else:
                part2.append(self.names[i])
        return max([part1, part2], key=len)

    def has_col(self, pos):
        """Returns True if alignment has position 'pos'"""
        return pos in self._cols

    def get_cols(self):
        """Returns a list of all columns"""
        return [self._cols[pos] for pos in self.positions]

    def remove(self, pos):
        """Return a variant sites from the alignment"""
        del self._cols[pos]
        self.positions.remove(pos)

    def __iter__(self):
        """Iterates through the positions and columns of the alignment"""
        def func():
            for i in self.positions:
                yield i, self._cols[i]
        return func()

    def iter_region(self, start=None, end=None):
        """
        Iterates through the positions and columns between 'start' and 'end'
        """
        if start is None:
            i = 0
        else:
            _, i = util.binsearch(self.positions, start)
        if end is None:
            j = None
        else:
            j2, j = util.binsearch(self.positions, end)
            if j == j2:
                j += 1

        def func():
            if i is None:
                return
            for pos in self.positions[i:j]:
                yield pos, self._cols[pos]

        return func()

    def __getitem__(self, pos):
        """Returns the column at position 'pos'"""
        return self._cols[pos]

    def __setitem__(self, pos, col):
        """Sets the column 'col' at position 'pos'"""
        self._cols[pos] = col
        self.positions.append(pos)
        self.positions.sort()

    def __delitem__(self, pos):
        """Removes a variant site from the alignment"""
        del self._cols[pos]
        self.positions.remove(pos)

    def __contains__(self, pos):
        """Returns True if position is within the laignment"""
        return pos in self._cols

    def write(self, filename):
        write_sites(filename, self)


def iter_sites(filename):
    """Iterate through a sites file"""
    infile = open_stream(filename)

    header = {}

    for line in infile:
        line = line.rstrip()

        if line and line[0].isdigit():
            break

        if line.startswith("NAMES") or line.startswith("#NAMES"):
            header["names"] = line.split("\t")[1:]

        elif line.startswith("REGION") or line.startswith("#REGION"):
            tokens = line.split("\t")
            header["chrom"] = tokens[1]
            header["region"] = list(map(int, tokens[2:]))

        elif line.startswith("RANGE") or line.startswith("#RANGE"):
            raise Exception("deprecated RANGE line, use REGION instead")

    yield header

    if line:
        for line in chain([line], infile):
            line = line.rstrip()
            tokens = line.split("\t")
            pos = int(tokens[0])
            col = tokens[1]

            yield pos, col

    infile.close()


def read_sites(filename, region=None):
    """Read a sites file"""

    reader = iter_sites(filename)
    header = next(reader)

    sites = Sites(names=header["names"], chrom=header["chrom"],
                  region=header["region"])
    if region:
        sites.region = region

    for pos, col in reader:
        if region and (pos < region[0]):
            continue
        if region and (pos > region[1]):
            break
        sites.append(pos, col)

    return sites


def write_sites(filename, sites):
    """Write a sites file"""

    out = open_stream(filename, "w")

    util.print_row("NAMES", *sites.names, out=out)
    util.print_row("REGION", sites.chrom, *sites.region, out=out)

    for pos, col in sites:
        util.print_row(pos, col, out=out)

    out.close()


def seqs2sites(seqs, chrom=None, region=None, start=None):
    """Convert FASTA object into a Sites object"""

    if start is None:
        start = 1
    if region is None:
        region = [start, start + seqs.alignlen() - 1]

    sites = Sites(names=list(seqs.keys()), chrom=chrom, region=region)

    for i in range(0, seqs.alignlen()):
        if is_variant(seqs, i):
            col = "".join(seqs[name][i] for name in seqs.names)
            sites.append(start + i, col)

    return sites


def sites2seqs(sites, default_char="A"):
    """
    Convert Sites object to FASTA object.
    """

    seqlen = sites.length()

    # create blank alignment
    seqs2 = []
    if default_char:
        for i in range(sites.nseqs()):
            seqs2.append([default_char] * seqlen)
    else:
        seq = ["ACGT"[random.randint(0, 3)] for i in range(seqlen)]
        for i in range(sites.nseqs()):
            seqs2.append(list(seq))

    # fill in sites
    for pos in sites.positions:
        for i, c in enumerate(sites[pos]):
            seqs2[i][pos - sites.region[0]] = c

    # make seqs dict
    seqs = fasta.FastaDict()
    for i, seq in enumerate(seqs2):
        seqs[sites.names[i]] = "".join(seq)

    return seqs


#=============================================================================
# simple LD functions

def find_high_freq_allele(col):
    counts = defaultdict(lambda: 0)
    for a in col:
        counts[a] += 1
    return max(list(counts.keys()), key=lambda x: counts[x])


def find_pair_allele_freqs(col1, col2):
    A1 = col1[0]
    B1 = col2[0]

    x = {}
    x[(0, 0)] = 0
    x[(0, 1)] = 0
    x[(1, 0)] = 0
    x[(1, 1)] = 0

    for i in range(len(col1)):
        a = int(col1[i] == A1)
        b = int(col2[i] == B1)
        x[(a, b)] += 1

    n = float(len(col1))
    for k, v in list(x.items()):
        x[k] = v / n

    return x


def calc_ld_D(col1, col2, x=None):
    if x is None:
        x = find_pair_allele_freqs(col1, col2)

    # D = x_11 - p_1 * q_1
    p1 = x[(0, 0)] + x[(0, 1)]
    q1 = x[(0, 0)] + x[(1, 0)]
    D = x[(0, 0)] - p1 * q1
    return D


def calc_ld_Dp(col1, col2, x=None):
    if x is None:
        x = find_pair_allele_freqs(col1, col2)

    # D = x_11 - p_1 * q_1
    p1 = x[(0, 0)] + x[(0, 1)]
    q1 = x[(0, 0)] + x[(1, 0)]
    D = x[(0, 0)] - p1 * q1

    p2 = 1.0 - p1
    q2 = 1.0 - q1

    if D == 0:
        return abs(D)
    elif D < 0:
        Dmax = min(p1 * q1, p2 * q2)
    else:
        Dmax = min(p1 * q2, p2 * q1)

    return D / Dmax


def calc_ld_r2(col1, col2, x=None):
    if x is None:
        x = find_pair_allele_freqs(col1, col2)

    p1 = x[(0, 0)] + x[(0, 1)]
    q1 = x[(0, 0)] + x[(1, 0)]
    D = x[(0, 0)] - p1 * q1

    p2 = 1.0 - p1
    q2 = 1.0 - q1

    return D*D / (p1*p2*q1*q2)


def calc_ld_matrix(cols, func):

    ncols = len(cols)
    ld = util.make_matrix(ncols, ncols, 0.0)

    for i in range(ncols):
        for j in range(i):
            ld[i][j] = ld[j][i] = func(cols[i], cols[j])

    return ld


#=============================================================================
# recombination


def find_tree_next_recomb(arg, pos, tree=False):
    """Returns the next recombination node in a local tree"""

    recomb = None
    nextpos = util.INF

    if tree:
        nodes = iter(arg)
    else:
        #nodes = arg.postorder_marginal_tree(pos-.5)
        nodes = arg.postorder_marginal_tree(pos)

    for node in nodes:
        if node.event == "recomb" and node.pos > pos and node.pos < nextpos:
            recomb = node
            nextpos = node.pos

    return recomb


def iter_visible_recombs(arg, start=None, end=None):
    """Iterates through visible recombinations in an ARG"""

    pos = start if start is not None else 0
    while True:
        recomb = find_tree_next_recomb(arg, pos)
        if recomb:
            yield recomb
            pos = recomb.pos
        else:
            break


#=============================================================================
# chromosome threads


def iter_chrom_thread(arg, node, by_block=True, use_clades=False):

    start = 0
    recombs = chain((x.pos for x in iter_visible_recombs(arg)), [arg.end-1])

    for recomb_pos in recombs:
        if start >= arg.end:
            continue
        tree = arg.get_marginal_tree(recomb_pos-.5)
        block = [start, recomb_pos+1]
        start = recomb_pos+1

        # find parent
        node2 = tree[node.name]
        last = node2
        parent = node2.parents[0]
        while len(parent.children) == 1:
            last = parent
            parent = parent.parents[0]

        # find sibling
        c = parent.children
        sib = c[1] if last == c[0] else c[0]
        while len(sib.children) == 1:
            sib = sib.children[0]

        if use_clades:
            branch = list(tree.leaf_names(sib))
        else:
            branch = sib.name

        if by_block:
            yield (branch, parent.age, block)
        else:
            for i in range(block[0], block[1]):
                yield (branch, parent.age)


def get_coal_point(arg, node, pos):

    tree = arg.get_marginal_tree(pos-.5)

    # find parent
    node2 = tree[node.name]
    last = node2
    parent = node2.parents[0]
    while len(parent.children) == 1:
        last = parent
        parent = parent.parents[0]

    # find sibling
    c = parent.children
    sib = c[1] if last == c[0] else c[0]
    while len(sib.children) == 1:
        sib = sib.children[0]

    return sib.name, parent.age


def iter_chrom_timeline(arg, node, by_block=True):

    for node, time, block in iter_chrom_thread(arg, node, by_block=True):
        if by_block:
            yield (block[0]+1, time)
            yield (block[1], time)
        else:
            for i in range(block[0]+1, block[1]+1):
                yield time


def get_clade_point(arg, node_name, time, pos):
    """Return a point along a branch in the ARG in terms of a clade and time"""

    if node_name in arg:
        tree = arg.get_marginal_tree(pos - .5)
        if (time > tree.root.age or
                (time == tree.root.age and node_name not in tree)):
            return (list(tree.leaf_names()), time)
        return (list(tree.leaf_names(tree[node_name])), time)
    else:
        return ([node_name], time)


#=============================================================================
# ARG operations


def make_trunk_arg(start, end, name="ind1"):
    """
    Returns a trunk genealogy
    """
    arg = arglib.ARG(start=start, end=end)
    arg.new_node(name, event="gene", age=0)
    return arg


def remove_arg_thread(arg, *chroms):
    """
    Remove a thread(s) from an ARG
    """
    remove_chroms = set(chroms)
    keep = [x for x in arg.leaf_names() if x not in remove_chroms]
    arg = arg.copy()
    arglib.subarg_by_leaf_names(arg, keep)
    return arglib.smcify_arg(arg)


def add_arg_thread(arg, new_name, thread, recombs):
    """Add a thread to an ARG"""

    def is_local_coal(arg, node, pos, local):
        return (len(node.children) == 2 and
                node.children[0] in local and
                arg.get_local_parent(node.children[0], pos-.5) == node and
                node.children[1] in local and
                arg.get_local_parent(node.children[1], pos-.5) == node and
                node.children[0] != node.children[1])

    def walk_up(arg, leaves, time, pos, ignore=None):

        order = dict((node, i) for i, node in enumerate(
            arg.postorder_marginal_tree(pos-.5)))
        local = set(order.keys())
        if ignore is not None and ignore in arg:
            ptr = arg[ignore]
            if ptr in local:
                local.remove(ptr)
                ptr = arg.get_local_parent(ptr, pos-.5)
            else:
                ptr = None

            while ptr and ptr in local:
                if (len(ptr.children) == 2 and
                    ((ptr.children[0] in local and
                      arg.get_local_parent(ptr.children[0], pos-.5) == ptr) or
                     (ptr.children[1] in local and
                      arg.get_local_parent(ptr.children[1], pos-.5) == ptr))):
                    break
                local.remove(ptr)
                ptr = arg.get_local_parent(ptr, pos-.5)

        queue = [(order[arg[x]], arg[x]) for x in leaves]
        seen = set(x[1] for x in queue)
        heapq.heapify(queue)

        while len(queue) > 1:
            i, node = heapq.heappop(queue)
            parent = arg.get_local_parent(node, pos-.5)
            if parent and parent not in seen:
                seen.add(parent)
                heapq.heappush(queue, (order[parent], parent))
        node = queue[0][1]
        parent = arg.get_local_parent(node, pos-.5)

        while parent and parent.age <= time:
            if is_local_coal(arg, parent, pos, local):
                break
            node = parent
            parent = arg.get_local_parent(node, pos-.5)

        if parent:
            if parent.age < time:
                print((leaves, parent.age, time, ignore))
                tree = arg.get_marginal_tree(pos-.5).get_tree()
                tree.write()
                treelib.draw_tree_names(tree, maxlen=8, minlen=8)
                assert False

        return node

    def add_node(arg, node, time, pos, event):

        node2 = arg.new_node(event=event, age=time, children=[node], pos=pos)
        if event == "coal":
            node2.pos = 0

        parent = arg.get_local_parent(node, pos-.5)
        if parent:
            node.parents[node.parents.index(parent)] = node2
            parent.children[parent.children.index(node)] = node2
            node2.parents.append(parent)
        else:
            node.parents.append(node2)

        return node2

    arg_recomb = dict((x.pos, x) for x in iter_visible_recombs(arg))
    recomb_clades = [
        (pos-1, None) + get_clade_point(arg, rnode, rtime, pos-1)
        for pos, rnode, rtime in recombs] + [
        (node.pos, node.name) +
        get_clade_point(arg, node.name, node.age, node.pos)
        for node in iter_visible_recombs(arg)]
    recomb_clades.sort()

    # make initial tree
    arg2 = arg.get_marginal_tree(-1)
    arglib.remove_single_lineages(arg2)

    start = get_clade_point(arg, thread[0][0], thread[0][1], 0)
    node = walk_up(arg2, start[0], start[1], -1)
    node2 = add_node(arg2, node, start[1], -1, "coal")
    leaf = arg2.new_node(name=new_name, event="gene", age=0)
    leaf.parents.append(node2)
    node2.children.append(leaf)

    # add each recomb and re-coal
    for rpos, rname, rleaves, rtime in recomb_clades:
        if rpos in arg_recomb:
            # find re-coal for existing recomb

            if thread[rpos][1] != thread[rpos+1][1]:
                if rtime > min(thread[rpos][1], thread[rpos+1][1]):
                    print((">>", rtime, thread[rpos], thread[rpos+1]))
                    treelib.draw_tree_names(
                        arg.get_marginal_tree(rpos-.5).get_tree(),
                        maxlen=8, minlen=8)
                    treelib.draw_tree_names(
                        arg.get_marginal_tree(rpos+.5).get_tree(),
                        maxlen=8, minlen=8)
                    assert False

            node = arg_recomb[rpos]
            #local1 = set(arg.postorder_marginal_tree(rpos-.5))
            local2 = set(arg.postorder_marginal_tree(rpos+.5))
            last = node
            node = arg.get_local_parent(node, rpos+.5)
            while (not is_local_coal(arg, node, rpos+1, local2)):
                last = node
                node = arg.get_local_parent(node, rpos+.5)
            c = node.children
            child = c[0] if c[1] == last else c[1]
            #recoal = node

            cleaves, ctime = get_clade_point(
                arg, child.name, node.age, rpos-.5)

            # get local tree T^{n-1}_i and add new branch
            tree = arg.get_marginal_tree(rpos+.5)
            arglib.remove_single_lineages(tree)
            node_name, time = thread[rpos+1]
            node = tree[node_name]

            # add new branch
            node2 = add_node(tree, node, time, rpos+1, "coal")
            if not node2.parents:
                tree.root = node2
            leaf = tree.new_node(name=new_name, event="gene", age=0)
            leaf.parents.append(node2)
            node2.children.append(leaf)

            recomb = walk_up(tree, rleaves, rtime, rpos+1, new_name)

            if recomb == node2 and rtime == node2.age:
                # recomb and new coal-state are near each other
                # we must decide if recomb goes above or below coal-state

                # if this is a mediated SPR, then recomb goes below.
                # otherwise it goes above.

                # SPR is mediated if previous coal state is not recomb branch
                node_name, time = thread[rpos]
                if node2.children[0].name != node_name:
                    # this is a mediated coal
                    recomb = node2.children[0]

            coal = recomb.parents[0]
            c = coal.children
            child = c[0] if c[1] == recomb else c[1]

            # get coal point in T^n_i
            rleaves, rtime = get_clade_point(
                tree, recomb.name, rtime, rpos+1)
            cleaves, ctime = get_clade_point(
                tree, child.name, coal.age, rpos+1)

            node1 = walk_up(arg2, rleaves, rtime, rpos+1)
            node2 = walk_up(arg2, cleaves, ctime, rpos+1, node1.name)

        else:
            # find re-coal for new recomb

            assert rtime <= thread[rpos][1], (rtime, thread[rpos][1])

            if rleaves == [new_name]:
                # recomb on new branch, coal given thread
                cleaves, ctime = get_clade_point(
                    arg, thread[rpos+1][0], thread[rpos+1][1], rpos+.5)
                assert ctime >= rtime, (rtime, ctime)

                node1 = walk_up(arg2, rleaves, rtime, rpos+1)
                node2 = walk_up(arg2, cleaves, ctime, rpos+1, new_name)

            else:
                # recomb in ARG, coal on new branch
                cleaves = [new_name]
                ctime = thread[rpos+1][1]
                assert ctime >= rtime, (rtime, ctime)

                # NOTE: new_name is not ignored for walk_up on rleaves
                # because I do not want the recombination to be higher
                # than the coal point, which could happen if the recomb time
                # is the same as the current coal time.
                node1 = walk_up(arg2, rleaves, rtime, rpos+1)
                node2 = walk_up(arg2, cleaves, ctime, rpos+1, node1.name)

        assert node1.parents
        assert rtime <= ctime

        recomb = add_node(arg2, node1, rtime, rpos, "recomb")
        if node1 == node2:
            node2 = recomb
        coal = add_node(arg2, node2, ctime, rpos, "coal")

        recomb.parents.append(coal)
        coal.children.append(recomb)

        node, time = get_coal_point(arg2, arg2[new_name], rpos+1)
        assert time == thread[rpos+1][1], (time, thread[rpos+1][1])

    return arg2


def arg_lca(arg, leaves, time, pos, ignore=None):
    """Returns Least Common Ancestor for leaves in an ARG at position 'pos'"""

    def is_local_coal(arg, node, pos, local):
        return (len(node.children) == 2 and
                node.children[0] in local and
                arg.get_local_parent(node.children[0], pos-.5) == node and
                node.children[1] in local and
                arg.get_local_parent(node.children[1], pos-.5) == node and
                node.children[0] != node.children[1])

    order = dict((node, i) for i, node in enumerate(
        arg.postorder_marginal_tree(pos-.5)))
    local = set(order.keys())
    if ignore is not None and ignore in arg:
        ptr = arg[ignore]
        local.remove(ptr)
        ptr = arg.get_local_parent(ptr, pos-.5)

        while ptr and ptr in local:
            if (len(ptr.children) == 2 and
                ((ptr.children[0] in local and
                  arg.get_local_parent(ptr.children[0], pos-.5) == ptr) or
                 (ptr.children[1] in local and
                  arg.get_local_parent(ptr.children[1], pos-.5) == ptr))):
                break
            local.remove(ptr)
            ptr = arg.get_local_parent(ptr, pos-.5)

    queue = [(order[arg[x]], arg[x]) for x in leaves]
    seen = set(x[1] for x in queue)
    heapq.heapify(queue)

    while len(queue) > 1:
        i, node = heapq.heappop(queue)
        parent = arg.get_local_parent(node, pos-.5)
        if parent and parent not in seen:
            seen.add(parent)
            heapq.heappush(queue, (order[parent], parent))
    node = queue[0][1]
    parent = arg.get_local_parent(node, pos-.5)

    # walk up appropriate time if given
    if time is not None:
        while parent and parent.age <= time:
            if is_local_coal(arg, parent, pos, local):
                break
            node = parent
            parent = arg.get_local_parent(node, pos-.5)

        if parent:
            if parent.age < time:
                print((leaves, parent.age, time))
                tree = arg.get_marginal_tree(pos-.5).get_tree()
                tree.write()
                treelib.draw_tree_names(tree, maxlen=8, minlen=8)
                assert False

    return node


def find_recomb_coal(tree, last_tree, recomb_name=None, pos=None):
    """
    Find the recomb and coal points for the SPR between two trees

    Returns ((recomb_node_name, recomb_time), (coal_node_name, coal_time))
    """

    if recomb_name is None:
        recomb = find_tree_next_recomb(last_tree, pos-1, tree=True)
        recomb_name = recomb.name

    # find recomb node
    recomb_node = tree[recomb_name]
    recomb_time = recomb_node.age

    # find re-coal point
    coal = recomb_node.parents[0]
    while coal.name not in last_tree and coal.parents:
        coal = coal.parents[0]
    coal_time = coal.age

    # find coal branch in last_tree
    if coal.name not in last_tree:
        # coal above root
        coal_branch = last_tree.root.name
    else:
        ptr = last_tree[coal.name]
        while len(ptr.children) == 1:
            ptr = ptr.children[0]
        coal_branch = ptr.name

    # find recomb branch in tree
    recomb = tree[recomb_name]
    while len(recomb.children) == 1:
        recomb = recomb.children[0]
    recomb_branch = recomb.name

    return (recomb_branch, recomb_time), (coal_branch, coal_time)


def iter_arg_sprs(arg, start=None, end=None):
    """
    Iterates through the SPRs of an ARG

    Yields (block, tree, last_tree, spr)
    where spr = (recomb_node, recomb_time, coal_node, coal_time)
    """

    if start is None:
        start = arg.start
    if end is None:
        end = arg.end

    last_tree_full = None
    last_tree = None
    for block, tree_full in arglib.iter_local_trees(arg, start, end):
        if last_tree_full:
            recomb = next((x for x in tree_full if x.pos == block[0]))
            spr = find_recomb_coal(tree_full, last_tree_full,
                                   recomb_name=recomb.name)
        else:
            spr = None

        # get tree with only leaves and coalescent nodes
        tree = tree_full.copy()
        tree = arglib.remove_single_lineages(tree)

        yield block, tree, last_tree, spr

        last_tree_full = tree_full
        last_tree = tree


def get_local_node_mapping(tree, last_tree, spr):
    """
    Determine the mapping between nodes in local trees across ARG.

    A maps across local trees until it is broken (parent of recomb node).
    This method assumes tree and last_tree share the same node naming
    and do not contain intermediary nodes (i.e. single lineages).
    """
    if last_tree is None:
        # no mapping if last_tree is None
        return None

    else:
        (rname, rtime), (cname, ctime) = spr

        # assert ARG is SMC-style (no bubbles)
        assert rname != cname

        # recomb_parent is broken and does not map to anyone
        recomb_parent = last_tree[rname].parents[0]
        mapping = dict((node.name, node.name) for node in last_tree)
        mapping[recomb_parent.name] = None
        return mapping


#=============================================================================
# probabilities


def prob_recomb(tree, state, nlineages, times, time_steps, rho, recomb_time):

    nbranches, nrecombs, ncoals = nlineages
    node, a = state
    treelen_b = get_treelen_branch(tree, times, node, times[a], use_basal=True)
    treelen = get_treelen_branch(tree, times, node, times[a], use_basal=False)
    k = recomb_time
    w = max(a, times.index(tree.root.age))

    nbranches_k = nbranches[k] + int(k < a)
    nrecombs_k = nrecombs[k] + int(k <= a) + int(k == a < w)

    return (nbranches_k * time_steps[k] / float(nrecombs_k * treelen_b)
            * (1.0 - exp(- rho * max(treelen, 1.0))))


def prob_recoal(tree, state, nlineages, times, time_steps, popsizes,
                recomb_node, recomb_time, coal_time):

    nbranches, nrecombs, ncoals = nlineages
    node, a = state
    k = recomb_time
    b = coal_time

    if recomb_node == -1 or not tree[recomb_node].parents:
        recomb_parent_age = a
    else:
        recomb_parent_age = times.index(tree[recomb_node].parents[0].age)
        if recomb_node == node:
            recomb_parent_age = a
    assert recomb_parent_age == a, (recomb_parent_age, a)

    s = 0.0
    for m in range(k, b):
        nbranches_m = nbranches[m] + int(m < a) - int(m < recomb_parent_age)
        s += time_steps[m] * nbranches_m / (2.0 * popsizes[m])
    p = exp(- s)

    if b < len(time_steps) - 2:
        nbranches_b = nbranches[b] + int(b < a) - int(b < recomb_parent_age)
        ncoals_b = ncoals[b]
        p *= ((1.0 - exp(-time_steps[b] * nbranches_b / (2.0 * popsizes[b]))) /
              ncoals_b)

    return p


def iter_transition_recombs(tree, state1, state2, times):

    node1, a = state1
    node2, b = state2
    end_time = min(a, b)

    if node1 == node2:
        # y = v, k in [0, min(timei, last_timei)]
        # y = node, k in Sr(node)
        for k in range(times.index(tree[node1].age), end_time+1):
            yield node1, k

    for k in range(0, end_time+1):
        yield -1, k


def calc_transition_probs(tree, states, nlineages, times,
                          time_steps, popsizes, rho):
    """
    Calculate transition probabilities very literally for testing
    """

    tree = tree.copy()
    arglib.remove_single_lineages(tree)

    nstates = len(states)
    #ntimes = len(time_steps)
    #minlen = time_steps[0]
    treelen = sum(x.get_dist() for x in tree)
    nbranches, nrecombs, ncoals = nlineages

    # calculate full state transition matrix
    transprob = util.make_matrix(nstates, nstates, 0.0)
    for i in range(nstates):
        node1, a = states[i]
        #c = times.index(tree[node1].age)

        for j in range(nstates):
            node2, b = states[j]
            coal_time = b

            p = 0.0
            for recomb_node, recomb_time in iter_transition_recombs(
                    tree, states[i], states[j], times):
                p += (prob_recomb(tree, states[i], nlineages, times,
                                  time_steps, rho, recomb_time) *
                      prob_recoal(tree, states[i], nlineages, times,
                                  time_steps, popsizes,
                                  recomb_node, recomb_time, coal_time))

            # probability of no recomb
            if i == j:
                treelen = get_treelen_branch(tree, times, node1, times[a],
                                             use_basal=False)
                p += exp(-rho * max(treelen, 1.0))

            transprob[i][j] = log(p)

    return transprob


def get_recomb_transition_switch(tree, last_tree, spr, states1, states2,
                                 times):

    # SPR subtree moves out from underneath us
    # therefore therefore the new chromosome coalesces with
    # the branch above the subtree

    (recomb_branch, recomb_time), (coal_branch, coal_time) = spr

    # search up for parent
    recomb = last_tree[recomb_branch]
    parent = recomb.parents[0]
    b = times.index(parent.age)

    # find other child
    c = parent.children
    other = (c[0] if c[1] == recomb else c[1])

    # find new state in tree
    if other.name == coal_branch:
        next_state = (tree[other.name].parents[0].name, b)
    else:
        next_state = (other.name, b)

    a = states2.index((recomb_branch, recomb_time))
    b = states2.index(next_state)
    return (a, b)


def calc_transition_probs_switch(tree, last_tree, recomb_name,
                                 states1, states2,
                                 nlineages, times,
                                 time_steps, popsizes, rho):

    treelen = get_treelen(last_tree, times)
    nbranches, nrecombs, ncoals = nlineages
    (recomb_branch, recomb_time), (coal_branch, coal_time) = \
        find_recomb_coal(tree, last_tree, recomb_name=recomb_name)

    k = times.index(recomb_time)
    coal_time = times.index(coal_time)

    last_tree2 = last_tree.copy()
    arglib.remove_single_lineages(last_tree2)
    tree2 = tree.copy()
    arglib.remove_single_lineages(tree2)

    # compute transition probability matrix
    transprob = util.make_matrix(len(states1), len(states2), -util.INF)

    determ = get_deterministic_transitions(states1, states2, times,
                                           tree2, last_tree2,
                                           recomb_branch, k,
                                           coal_branch, coal_time)

    for i, (node1, a) in enumerate(states1):
        if (node1, a) == (recomb_branch, k):
            # probabilistic transition case (recomb case)
            spr = (recomb_branch, k), (coal_branch, coal_time)
            recomb_next_states = get_recomb_transition_switch(
                tree2, last_tree2, spr, states1, states2, times)

            # placeholders
            transprob[i][recomb_next_states[0]] = log(.5)
            transprob[i][recomb_next_states[1]] = log(.5)

        elif (node1, a) == (coal_branch, coal_time):
            # probabilistic transition case (re-coal case)

            # determine if node1 is still here or not
            last_recomb = last_tree2[recomb_branch]
            last_parent = last_recomb.parents[0]
            if last_parent.name == node1:
                # recomb breaks node1 branch, we need to use the other child
                c = last_parent.children
                node3 = c[0].name if c[1] == last_recomb else c[1].name
            else:
                node3 = node1

            # find parent of recomb_branch and node1
            last_parent_age = times.index(last_parent.age)
            parent = tree2[recomb_branch].parents[0]
            assert parent == tree2[node3].parents[0]

            # treelen of T^n_{i-1}
            blen = times[a]
            treelen2 = treelen + blen
            if node1 == last_tree2.root.name:
                treelen2 += blen - last_tree2.root.age
                treelen2 += time_steps[a]
            else:
                treelen2 += time_steps[times.index(last_tree2.root.age)]

            for j, (node2, b) in enumerate(states2):
                transprob[i][j] = 0.0
                if not ((node2 == recomb_branch and b >= k) or
                        (node2 == node3 and b == a) or
                        (node2 == parent.name and b == a)):
                    continue

                # get lineage counts
                # remove recombination branch and add new branch
                kbn = nbranches[b]
                kcn = ncoals[b] + 1
                if times[b] < parent.age:
                    kbn -= 1
                    kcn -= 1
                if b < a:
                    kbn += 1

                twon = 2.0 * popsizes[b]

                transprob[i][j] = (
                    (1.0 - exp(- time_steps[b] * kbn / twon)) / kcn *
                    exp(- sum(time_steps[m] * (nbranches[m] + 1
                              - (1 if m < last_parent_age else 0))
                              / (2.0 * popsizes[m])
                              for m in range(k, b))))

            # normalize row to ensure they add up to one
            tot = sum(transprob[i])
            for j in range(len(states2)):
                x = transprob[i][j]
                if tot > 0.0 and x > 0.0:
                    transprob[i][j] = log(x / tot)
                else:
                    transprob[i][j] = -1e1000

        else:
            # deterministic transition
            assert determ[i] != -1, determ
            transprob[i][determ[i]] = 0.0

    return transprob


def get_deterministic_transitions(states1, states2, times,
                                  tree, last_tree,
                                  recomb_branch, recomb_time,
                                  coal_branch, coal_time):

    # recomb_branch in tree and last_tree
    # coal_branch in last_tree

    state2_lookup = util.list2lookup(states2)

    next_states = []
    for i, state1 in enumerate(states1):
        node1, a = state1

        if (node1, a) == (coal_branch, coal_time):
            # not a deterministic case
            next_states.append(-1)

        elif node1 != recomb_branch:
            # SPR only removes a subset of descendents, if any
            # trace up from remaining leaf to find correct new state

            node = last_tree.nodes.get(node1, None)
            if node is None:
                print(node1)
                treelib.draw_tree_names(last_tree.get_tree(),
                                        minlen=8, maxlen=8)
                raise Exception("unknown node name '%s'" % node1)

            if node.is_leaf():
                # SPR can't disrupt leaf branch
                node2 = node1

            else:
                child1 = node.children[0]
                child2 = node.children[1]

                if recomb_branch == child1.name:
                    # right child is not disrupted
                    node2 = child2.name

                elif recomb_branch == child2.name:
                    # left child is not disrupted
                    node2 = child1.name

                else:
                    # node is not disrupted
                    node2 = node1

            # optionally walk up
            if ((coal_branch == node1 or coal_branch == node2) and
                    coal_time <= a):
                # coal occurs under us
                node2 = tree[node2].parents[0].name
            next_states.append(state2_lookup[(node2, a)])

        else:
            # SPR is on same branch as new chromosome
            if recomb_time >= a:
                # we move with SPR subtree
                # TODO: we could probabilistically have subtree move
                # out from underneath.
                next_states.append(state2_lookup[(recomb_branch, a)])

            else:
                # SPR should not be able to coal back onto same branch
                # this would be a self cycle
                assert coal_branch != node1

                # SPR subtree moves out from underneath us
                # therefore therefore the new chromosome coalesces with
                # the branch above the subtree

                # search up for parent
                recomb = last_tree[recomb_branch]
                parent = recomb.parents[0]
                b = times.index(parent.age)

                # find other child
                c = parent.children
                other = (c[0] if c[1] == recomb else c[1])

                # find new state in tree
                if other.name == coal_branch:
                    next_state = (tree[other.name].parents[0].name, b)
                else:
                    next_state = (other.name, b)

                next_states.append(state2_lookup[next_state])

    return next_states


def calc_state_priors(tree, states, nlineages,
                      times, time_steps, popsizes, rho):
    """Calculate state priors"""

    priormat = [
        log((1 - exp(- time_steps[b] * nlineages[0][b] /
                     (2.0 * popsizes[b]))) / nlineages[2][b] *
            exp(-sum(time_steps[m] * nlineages[0][m] /
                     (2.0 * popsizes[m])
                     for m in range(0, b))))
        for node, b in states]

    return priormat


def est_arg_popsizes(arg, times=None, popsize_mu=1e4, popsize_sigma=.5e4):

    nleaves = len(list(arg.leaves()))
    assert times
    eps = 1e-3

    def get_local_children(node, pos, local):
        return set(child for child in arg.get_local_children(node, pos)
                   if child in local)

    def get_parent(node, pos, local):
        parent = arg.get_local_parent(node, pos)
        while len(get_local_children(parent, pos, local)) == 1:
            parent = arg.get_local_parent(parent, pos)
        return parent

    ntimes = len(times)
    time_steps = [times[i] - times[i-1]
                  for i in range(1, ntimes)]

    ncoals = [0] * ntimes
    k_lineages = [0] * ntimes

    # loop through sprs
    for recomb_pos, (rnode, rtime), (cnode, ctime), local in \
            arglib.iter_arg_sprs(arg, use_local=True):
        i, _ = util.binsearch(times, ctime)
        ncoals[i] += 1

        recomb_node = arg[rnode]
        broken_node = get_parent(recomb_node, recomb_pos-eps, local)
        coals = [0.0] + [
            node.age for node in local
            if len(get_local_children(node, recomb_pos-eps, local)) == 2]

        coals.sort()
        nlineages = list(range(nleaves, 0, -1))
        assert len(nlineages) == len(coals)

        # subtract broken branch
        r = coals.index(recomb_node.age)
        r2 = coals.index(broken_node.age)
        for i in range(r, r2):
            nlineages[i] -= 1

        # get average number of branches in the time interval
        data = list(zip(coals, nlineages))
        for t in times[1:]:
            data.append((t, "time step"))
        data.sort()

        lineages_per_time = []
        counts = []
        last_lineages = 0
        last_time = 0.0
        for a, b in data:
            if b != "time step":
                if a > last_time:
                    counts.append((last_lineages, a - last_time))
                last_lineages = b
            else:
                counts.append((last_lineages, a - last_time))
                s = sum(u * v for u, v in counts)
                total_time = sum(v for u, v in counts)
                if s == 0.0:
                    lineages_per_time.append(last_lineages)
                else:
                    lineages_per_time.append(s / total_time)
                counts = []
            last_time = a

        assert len(lineages_per_time) == len(time_steps)

        r, _ = util.binsearch(times, rtime)
        c, _ = util.binsearch(times, ctime)
        for j in range(r, c):
            k_lineages[j] += lineages_per_time[j]

    # add first tree
    tree = arg.get_marginal_tree(arg.start)
    arglib.remove_single_lineages(tree)
    for node in tree:
        a, _ = util.binsearch(times, node.age)
        if not node.parents:
            ncoals[a] += 1
            continue
        b, _ = util.binsearch(times, node.parents[0].age)
        for i in range(a, b):
            k_lineages[i] += 1
        if not node.is_leaf():
            ncoals[a] += 1

    try:
        import scipy.optimize

        popsizes = []
        for j in range(len(time_steps)):
            A = - 1.0 / popsize_sigma / popsize_sigma
            B = popsize_mu / popsize_sigma / popsize_sigma
            C = - ncoals[j]
            D = time_steps[j] * k_lineages[j] / 2.0

            def func(x):
                return A*x*x*x + B*x*x + C*x + D

            try:
                popsizes.append(
                    scipy.optimize.brentq(func, 0, 2.0*popsize_mu))
            except:
                popsizes.append(popsize_mu)
    except:
        raise
        popsizes = [(time_steps[j] / 2.0 / ncoals[j] * k_lineages[j]
                     if ncoals[j] > 0 else util.INF)
                    for j in range(len(time_steps))]

    return popsizes

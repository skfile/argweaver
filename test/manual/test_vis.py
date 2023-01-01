
import sys, os
import random
import importlib

try:
    import arghmm
except ImportError:
    os.chdir("..")
    import arghmm
importlib.reload(arghmm)

from rasmus.common import *
from rasmus import stats, hmm
from rasmus.testing import *

from compbio import coal, arglib
from compbio.vis import argvis
importlib.reload(argvis)

import summon
from summon.core import *


#=============================================================================

def get_mapping(tree1, tree2, pos, times):
    recomb, coal = arghmm.find_recomb_coal(tree2, tree1, pos=pos)
    states1 = list(arghmm.iter_coal_states(tree1, times))
    states2 = list(arghmm.iter_coal_states(tree2, times))
    
    for i, (node1, time1) in enumerate(states1):
        j = arghmm.get_deterministic_transition(
            (node1, time1), states2, times, tree2, tree1,
            recomb[0], times.index(recomb[1]),
            coal[0], times.index(coal[1]))
        node2, time2 = states2[j]
        yield (node1, time1), (node2, time2)

def draw_mapping(tree1, tree2, layout1, layout2, times, mapping):

    for (node1, time1), (node2, time2) in mapping:
        x1 = layout1[tree1[node1]][0]
        x2 = layout2[tree2[node2]][0]
        y1 = times[time1]
        y2 = times[time2]
            
        win.add_group(group(line_strip(color(0,1,0,.5),
                                       x1, y1,
                                       (x1+x2)/2.0, 100+(y1+y2)/2.0,
                                       x2, y2)))

def move_layout(layout, x=0, y=0):
    for node, (nx, ny) in list(layout.items()):
        layout[node] = (nx + x, ny + y)


def get_time_points(arg, ntimes=20):
    times2 = arghmm.get_time_points(ntimes=ntimes)
    times3 = sorted(unique([x.age for x in arg]))

    times = []
    for x in times2:
        i, j = util.binsearch(times3, x)
        if i is None: i = j
        if j is None: j = i
        if abs(times3[i] - x) < 1:
            times.append(times3[i])
        elif abs(times3[j] - x) < 1:
            times.append(times3[j])
        else:
            times.append(x)
    return times
    

#=============================================================================
if 1:
    #times = arghmm.get_time_points(ntimes=20)
    arg = arglib.read_arg("test/data/sample.arg")
    seqs = read_fasta("test/data/sample.fa")
    
    trees = list(arglib.iter_tree_tracks(arg, convert=True))

    # draw mappings
    win = argvis.show_tree_track(trees)

    nleaves = ilen(arg.leaves())
    for i in range(len(trees)-1):
        block1, _tree1 = trees[i]
        block2, _tree2 = trees[i + 1]
        pos = block2[0]
        tree1 = arg.get_marginal_tree(pos-.5)
        tree2 = arg.get_marginal_tree(pos+.5)
        layout1 = argvis.layout_arg(tree1)
        layout2 = argvis.layout_arg(tree2)
        #layout1 = treelib.layout_tree_vertical(
        #    treelib.layout_tree(tree1, xscale=1, yscale=1), leaves=0)
        #layout2 = treelib.layout_tree_vertical(
        #    treelib.layout_tree(tree2, xscale=1, yscale=1), leaves=0)        
        move_layout(layout1, x=i*(nleaves+2))
        move_layout(layout2, x=(i+1)*(nleaves+2))
        mapping = get_mapping(tree1, tree2, pos, times)
        draw_mapping(tree1, tree2, layout1, layout2, times, mapping)
        

#=============================================================================
if 0:
    k = 10
    n = 1e4
    rho = 1.5e-8 * 100
    mu = 2.5e-8
    length = 1000
    
    arg = arglib.sample_arg(k, n, rho, start=0, end=length)
    arglib.write_arg("tmp/a.arg", arg)
    muts = arglib.sample_arg_mutations(arg, mu)
    seqs = arglib.make_alignment(arg, muts)

    #arg = arglib.read_arg("tmp/a.arg")
    #arg.set_ancestral()
    #find_recomb_coal(tree, last_tree, recomb_name=None, pos=None)

    times = arghmm.get_time_points(30, maxtime=60e3)
    arghmm.discretize_arg(arg, times)

    # get recombs
    recombs = list(x.pos for x in arghmm.iter_visible_recombs(arg))
    print("recomb", recombs)

    pos = recombs[0] + 1
    tree = arg.get_marginal_tree(pos-.5)
    last_tree = arg.get_marginal_tree(pos-1-.5)
    
    r, c = arghmm.find_recomb_coal(tree, last_tree, pos=pos)

    #treelib.draw_tree_names(last_tree.get_tree(), minlen=5, maxlen=5)
    #treelib.draw_tree_names(tree.get_tree(), minlen=5, maxlen=5)

    model = arghmm.ArgHmm(arg, seqs, new_name="n%d" % (k-1), times=times)


    #================================================================
    win = summon.Window()

    layout = argvis.layout_arg(last_tree)
    win.add_group(argvis.draw_arg(last_tree, layout))

    layout2 = argvis.layout_arg(tree)
    for node, (x,y) in list(layout2.items()):
        layout2[node] = (x+20, y)
    
    win.add_group(argvis.draw_arg(tree, layout2))

    recomb = tree[r[0]]
    x, y = layout2[recomb]
    win.add_group(argvis.draw_mark(x, y, col=(0, 0, 1)))

    coal = last_tree[c[0]]
    x, y = layout[coal]
    win.add_group(argvis.draw_mark(x, y, col=(0, 1, 0)))


    mapping = {}
    for i, (node1, time1) in enumerate(model.states[pos-1]):
        j = arghmm.get_deterministic_transition(
            (node1, time1), model.states[pos], model.times, tree, last_tree,
            r[0], times.index(r[1]),
            c[0], times.index(c[1]))
        node2, time2 = model.states[pos][j]
        mapping[(node1, time1)] = (node2, time2)

        x1 = layout[last_tree[node1]][0]
        x2 = layout2[tree[node2]][0]
        y1 = model.times[time1]
        y2 = model.times[time2]
            
        win.add_group(group(line_strip(color(0,1,0,.5),
                                       x1, y1,
                                       (x1+x2)/2.0, 100+(y1+y2)/2.0,
                                       x2, y2)))

    win.home("exact")



#===========================================================================   
if 0:
    #times = arghmm.get_time_points(ntimes=20)
    arg = arglib.read_arg("test/data/sample.arg")
    seqs = read_fasta("test/data/sample.fa")

    times = sorted(unique([x.age for x in arg]))

    mu = 2.5e-8
    rho = 1.5e-8
    new_name = "n4"
    model = arghmm.ArgHmm(arg, seqs, new_name=new_name, times=times,
                          rho=rho, mu=mu)

    trees = list(arglib.iter_tree_tracks(arg, convert=True))

    # draw tree
    win = argvis.show_tree_track(trees, branch_click=True)
    


# inspect switch matrix
if 1:
    arg2 = arglib.read_arg("test/data/sample-prune.arg")
    path = read_ints("test/data/sample.thread")

    times = get_time_points(arg2, ntimes=20)

    # remove chrom
    #k = 5
    #keep = ["n%d" % i for i in range(k-1)]
    #arglib.subarg_by_leaf_names(arg2, keep)
    #arg2.set_ancestral()
    #arg2.prune()


    # load model
    mu = 2.5e-8
    rho = 1.5e-8
    new_name = "n4"
    model = arghmm.ArgHmm(arg2, seqs, new_name=new_name, times=times,
                          rho=rho, mu=mu)

    
    pos = 9910
    tree = arg2.get_marginal_tree(pos-.5)
    last_tree = arg2.get_marginal_tree(pos-1-.5)
    recomb = arghmm.find_tree_next_recomb(arg2, pos - 1)
    states1 = model.states[pos-1]
    states2 = model.states[pos]
    model.check_local_tree(pos)
    
    mat = arghmm.calc_transition_probs_switch(
        tree, last_tree, recomb.name,
        states1, states2,
        model.nlineages, model.times,

        model.time_steps, model.popsizes, model.rho)

    #pos2 = 7000
    #n = model.get_num_states(pos2)
    #mat2 = [[model.prob_transition(pos2-1, i, pos2, j)
    #         for j in range(n)] for i in range(n)]

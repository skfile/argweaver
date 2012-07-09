
#include "stdio.h"
#include "local_tree.h"


namespace arghmm {


//=============================================================================
// tree methods


// Counts the number of lineages in a tree for each time segment
//
// NOTE: Nodes in the tree are not allowed to exist at the top time point 
// point (ntimes - 1).
//
// tree      -- local tree to count
// ntimes    -- number of time segments
// nbranches -- number of branches that exists between time i and i+1
// nrecombs  -- number of possible recombination points at time i
// ncoals    -- number of possible coalescing points at time i
void count_lineages(const LocalTree *tree, int ntimes,
                    int *nbranches, int *nrecombs, int *ncoals)
{
    const LocalNode *nodes = tree->nodes;

    // initialize counts
    for (int i=0; i<ntimes; i++) {
        nbranches[i] = 0;
        nrecombs[i] = 0;
        ncoals[i] = 0;
    }

    // iterate over the branches of the tree
    for (int i=0; i<tree->nnodes; i++) {
        assert(nodes[i].age < ntimes - 1);
        const int parent = nodes[i].parent;
        const int parent_age = ((parent == -1) ? ntimes - 2 : 
                                nodes[parent].age);
        
        // add counts for every segment along branch
        for (int j=nodes[i].age; j<parent_age; j++) {
            nbranches[j]++;
            nrecombs[j]++;
            ncoals[j]++;
        }

        // recomb and coal are also allowed at the top of a branch
        nrecombs[parent_age]++;
        ncoals[parent_age]++;
        if (parent == -1)
            nbranches[parent_age]++;
    }
    
    // ensure last time segment always has one branch
    nbranches[ntimes - 1] = 1;
}


// Calculate tree length according to ArgHmm rules
double get_treelen(const LocalTree *tree, const double *times, int ntimes,
                   bool use_basal)
{
    double treelen = 0.0;
    const LocalNode *nodes = tree->nodes;
    
    for (int i=0; i<tree->nnodes; i++) {
        int parent = nodes[i].parent;
        int age = nodes[i].age;
        if (parent == -1) {
            // add basal stub
            if (use_basal)
                treelen += times[age+1] - times[age];
        } else {
            treelen += times[nodes[parent].age] - times[age];
        }
    }
    
    return treelen;
}


double get_treelen_branch(const LocalTree *tree, const double *times, 
                          int ntimes, int node, int time, 
                          double treelen, bool use_basal)
{

    if (treelen < 0.0)
        treelen = get_treelen(tree, times, ntimes);
    
    int rooti = tree->nodes[tree->root].age;
    double root_time = times[rooti+1] - times[rooti];
    treelen -= root_time;  // discount root time

    double blen = times[time];
    double treelen2 = treelen + blen;
    if (node == tree->root) {
        treelen2 += blen - times[tree->nodes[tree->root].age];
        root_time = times[time+1] - times[time];
    } else {
        rooti = tree->nodes[tree->root].age;
        root_time = times[rooti+1] - times[rooti];
    }
    
    if (use_basal)
        return treelen2 + root_time;
    else
        return treelen2;
}


double get_basal_branch(const LocalTree *tree, const double *times, int ntimes,
                        int node, int time)
{
    double root_time;

    if (node == tree->root) {
        root_time = times[time+1] - times[time];
    } else {
        int rooti = tree->nodes[tree->root].age;
        root_time = times[rooti+1] - times[rooti];
    }
    
    return root_time;
}


void apply_spr(LocalTree *tree, const Spr &spr)
{
    // before SPR:
    //       bp          cp
    //      / \           \       .
    //     rc              c
    //    / \                     .
    //   r   rs

    // after SPR:
    //    bp         cp
    //   /  \         \           .
    //  rs             rc
    //                /  \        .
    //               r    c

    // key:
    // r = recomb branch
    // rs = sibling of recomb branch
    // rc = recoal node (broken node)
    // bp = parent of broken node
    // c = coal branch
    // cp = parent of coal branch


    LocalNode *nodes = tree->nodes;

    // recoal is also the node we are breaking
    int recoal = nodes[spr.recomb_node].parent;

    // find recomb node sibling and broke node parent
    int *c = nodes[recoal].child;
    int other = (c[0] == spr.recomb_node ? 1 : 0);
    int recomb_sib = c[other];
    int broke_parent =  nodes[recoal].parent;


    // fix recomb sib pointer
    nodes[recomb_sib].parent = broke_parent;

    // fix parent of broken node
    int x = 0;
    if (broke_parent != -1) {
        c = nodes[broke_parent].child;
        x = (c[0] == recoal ? 0 : 1);
        nodes[broke_parent].child[x] = recomb_sib;
    }

    // reuse node as recoal
    if (spr.coal_node == recoal) {
        // we just broke coal_node, so use recomb_sib
        nodes[recoal].child[other] = recomb_sib;
        nodes[recoal].parent = nodes[recomb_sib].parent;
        nodes[recomb_sib].parent = recoal;
        if (broke_parent != -1)
            nodes[broke_parent].child[x] = recoal;
    } else {
        nodes[recoal].child[other] = spr.coal_node;
        nodes[recoal].parent = nodes[spr.coal_node].parent;
        nodes[spr.coal_node].parent = recoal;
        
        // fix coal_node parent
        int parent = nodes[recoal].parent;
        if (parent != -1) {
            c = nodes[parent].child;
            if (c[0] == spr.coal_node) 
                c[0] = recoal;
            else
                c[1] = recoal;
        }
    }
    nodes[recoal].age = spr.coal_time;   
    
    // set tree data
    tree->set_root();
}


//=============================================================================
// local trees methods

bool remove_null_spr(LocalTrees *trees, LocalTrees::iterator it)
{
    // look one tree ahead
    LocalTrees::iterator it2 = it;
    ++it2;
    if (it2 == trees->end())
        return false;

    // get spr from next tree, skip it if it is not null
    Spr *spr2 = &it2->spr;
    if (!spr2->is_null())
        return false;

    int nnodes = it2->tree->nnodes;
        
    if (it->mapping == NULL) {
        // it2 will become first tree and therefore does not need a mapping
        delete [] it2->mapping;
        it2->mapping = NULL;
    } else {
        // compute transitive mapping
        int *M1 = it->mapping;
        int *M2 = it2->mapping;
        int mapping[nnodes];
        for (int i=0; i<nnodes; i++) {
            if (M1[i] != -1)
                mapping[i] = M2[M1[i]];
            else
                mapping[i] = -1;
        }
        
        // set mapping
        for (int i=0; i<nnodes; i++)
            M2[i] = mapping[i];
        
        // copy over non-null spr
        *spr2 = it->spr;
        assert(!spr2->is_null());
    }


    // delete this tree
    it2->blocklen += it->blocklen;
    it->clear();
    trees->trees.erase(it);
    
    return true;
}



// Removes trees with null SPRs from the local trees
void remove_null_sprs(LocalTrees *trees)
{
    for (LocalTrees::iterator it=trees->begin(); it != trees->end();) {
        LocalTrees::iterator it2 = it;
        ++it2;
        remove_null_spr(trees, it);
        it = it2;
    }
}



LocalTrees::LocalTrees(int **ptrees, int**ages, int **isprs, int *blocklens,
                       int ntrees, int nnodes, int capacity, int start) :
    start_coord(start),
    nnodes(nnodes)
{
    if (capacity < nnodes)
        capacity = nnodes;

    // copy data
    int pos = start;
    for (int i=0; i<ntrees; i++) {
        end_coord = pos + blocklens[i];
        
        // make mapping
        int *mapping = NULL;
        if (i > 0) {
            mapping = new int [nnodes];
            make_node_mapping(ptrees[i-1], nnodes, isprs[i][0], mapping);
        }

        trees.push_back(LocalTreeSpr(new LocalTree(ptrees[i], nnodes, ages[i],
                                                   capacity),
                                     isprs[i], blocklens[i], mapping));

        pos = end_coord;
    }

    set_default_seqids();
}


LocalTrees *partition_local_trees(LocalTrees *trees, int pos,
                                  LocalTrees::iterator it, int it_start)
{
    // create new local trees
    LocalTrees *trees2 = new LocalTrees(pos, trees->end_coord, trees->nnodes);
    trees2->seqids.insert(trees2->seqids.end(), trees->seqids.begin(),
                          trees->seqids.end());

    // splice trees over
    trees2->trees.splice(trees2->begin(), trees->trees, it, trees->end());
    
    // copy first tree back
    LocalTrees::iterator it2 = trees2->begin();
    if (pos > it_start) {
        LocalTree *tree = it2->tree;
        LocalTree *last_tree = new LocalTree(tree->nnodes, tree->capacity);
        last_tree->copy(*tree);

        int *mapping = NULL;
        if (it2->mapping) {
            mapping = new int[trees->nnodes];
            for (int i=0; i<trees->nnodes; i++)
                mapping[i] = it2->mapping[i];
        }
        
        trees->trees.push_back(
            LocalTreeSpr(last_tree, it2->spr, pos - it_start, mapping));
    }
    trees->end_coord = pos;

    // modify first tree of trees2 
    it2->mapping = NULL;
    it2->spr.set_null();
    it2->blocklen -= pos - it_start;
    assert(it2->blocklen > 0);

    assert_trees(trees);
    assert_trees(trees2);

    return trees2;
}


LocalTrees *partition_local_trees(LocalTrees *trees, int pos)
{
    // find break point
    int end = trees->start_coord;
    for (LocalTrees::iterator it=trees->begin(); it != trees->end(); ++it) {
        int start = end;
        end += it->blocklen;
        
        if (start <= pos && pos < end) {
            // break point found, perform partition
            return partition_local_trees(trees, pos, it, start);
        }
    }

    // break point was not found
    return NULL;
}


// Returns a mapping from nodes in tree1 to equivalent nodes in tree2
// If no equivalent is found, node maps to -1
void map_congruent_trees(const LocalTree *tree1, const int *seqids1,
                         const LocalTree *tree2, const int *seqids2, 
                         int *mapping)
{
    const int nleaves1 = tree1->get_num_leaves();
    const int nleaves2 = tree2->get_num_leaves();

    for (int i=0; i<tree1->nnodes; i++)
        mapping[i] = -1;

    // reconcile leaves
    for (int i=0; i<nleaves1; i++) {
        const int seqid = seqids1[i];
        mapping[i] = -1;
        for (int j=0; j<nleaves2; j++) {
            if (seqids2[j] == seqid) {
                mapping[i] = j;
                break;
            }
        }
    }

    // postorder iterate over full tree to reconcile internal nodes
    int order[tree1->nnodes];
    tree1->get_postorder(order);
    LocalNode *nodes = tree1->nodes;
    for (int i=0; i<tree1->nnodes; i++) {
        const int j = order[i];
        const int *child = nodes[j].child;
        
        if (!nodes[j].is_leaf()) {
            if (mapping[child[0]] != -1) {
                if (mapping[child[1]] != -1) {
                    // both children mapping, so we map to their LCA
                    mapping[j] = tree2->nodes[mapping[child[0]]].parent;
                    assert(tree2->nodes[mapping[child[0]]].parent ==
                           tree2->nodes[mapping[child[1]]].parent);
                } else {
                    // single child maps, copy its mapping
                    mapping[j] = mapping[child[0]];
                }
            } else {
                if (mapping[child[1]] != -1) {
                    // single child maps, copy its mapping
                    mapping[j] = mapping[child[1]];
                } else {
                    // neither child maps, so neither do we
                    mapping[j] = -1;
                }
            }
        }
    }
}


// appends the data in 'trees2' to 'trees'
// trees2 is then empty
// TODO: what if their seqids are incompatiable?
// Do I have to remap the ids of one of them to match the other?
void append_local_trees(LocalTrees *trees, LocalTrees *trees2)
{
    // ensure seqids are the same
    for (unsigned int i=0; i<trees->seqids.size(); i++)
        assert(trees->seqids[i] == trees2->seqids[i]);
    assert(trees->nnodes == trees2->nnodes);

    // move trees2 onto end of trees
    LocalTrees::iterator it = trees->end();
    --it;
    trees->trees.splice(trees->end(), 
                        trees2->trees, trees2->begin(), trees2->end());
    trees->end_coord = trees2->end_coord;
    trees2->end_coord = trees2->start_coord;
    
    // set the mapping the newly neighboring trees
    LocalTrees::iterator it2 = it;
    ++it2;
    if (it2->mapping == NULL)
        it2->mapping = new int [trees2->nnodes];
    map_congruent_trees(it->tree, &trees->seqids[0],
                        it2->tree, &trees2->seqids[0], it2->mapping);

    assert(remove_null_spr(trees, it));

    assert_trees(trees);
    assert_trees(trees2);
}


//=============================================================================
// assert functions

// Asserts that a postorder traversal is correct
bool assert_tree_postorder(LocalTree *tree, int *order)
{
    if (tree->root != order[tree->nnodes-1])
        return false;

    char seen[tree->nnodes];
    for (int i=0; i<tree->nnodes; i++)
        seen[i] = 0;

    for (int i=0; i<tree->nnodes; i++) {
        int node = order[i];
        seen[node] = 1;
        if (!tree->nodes[node].is_leaf()) {
            if (! seen[tree->nodes[node].child[0]] ||
                ! seen[tree->nodes[node].child[1]])
                return false;
        }
    }
    
    return true;
}


// Asserts structure of tree
bool assert_tree(const LocalTree *tree)
{
    LocalNode *nodes = tree->nodes;
    int nnodes = tree->nnodes;

    for (int i=0; i<nnodes; i++) {
        int *c = nodes[i].child;

        // assert parent child links
        if (c[0] != -1) {
            if (c[0] < 0 || c[0] >= nnodes)
                return false;
            if (nodes[c[0]].parent != i)
                return false;
        }
        if (c[1] != -1) {
            if (c[1] < 0 || c[1] >= nnodes)
                return false;
            if (nodes[c[1]].parent != i)
                return false;
        }

        // check parent

        // check root
        if (nodes[i].parent == -1) {
            if (tree->root != i)
                return false;
        } else {
            if (nodes[i].parent < 0 || nodes[i].parent >= nnodes)
                return false;
        }
    }

    // check root
    if (nodes[tree->root].parent != -1)
        return false;
    
    return true;
}


bool assert_spr(const LocalTree *last_tree, const LocalTree *tree, 
                const Spr *spr, const int *mapping)
{
    LocalNode *last_nodes = last_tree->nodes;

    if (spr->recomb_node == -1)
        assert(false);

    // recomb baring branch cannot be broken
    assert(mapping[spr->recomb_node] != -1);

    // coal time is older than recomb time
    if (spr->recomb_time > spr->coal_time)
        assert(false);

    // ensure recomb is within branch
    if (spr->recomb_time > last_nodes[last_nodes[spr->recomb_node].parent].age
        || spr->recomb_time < last_nodes[spr->recomb_node].age)
        assert(false);
    
    // ensure coal is within branch
    if (spr->coal_time < last_nodes[spr->coal_node].age)
        assert(false);
    if (last_nodes[spr->coal_node].parent != -1) {
        if (spr->coal_time > last_nodes[last_nodes[spr->coal_node].parent].age)
            assert(false);
    }

    // ensure spr matches the trees
    int recoal = tree->nodes[mapping[spr->recomb_node]].parent;
    int *c = tree->nodes[recoal].child;
    int other = (c[0] == mapping[spr->recomb_node] ? c[1] : c[0]);
    if (mapping[spr->coal_node] != -1) {
        // coal node is not broken, so it should map correctly
        assert(other == mapping[spr->coal_node]);
    } else {
        // coal node is broken
        int broken = last_tree->nodes[spr->recomb_node].parent;
        int *c = last_tree->nodes[broken].child;
        int last_other = (c[0] == spr->recomb_node ? c[1] : c[0]);
        assert(mapping[last_other] != -1);
        assert(tree->nodes[mapping[last_other]].parent == recoal);
    }
        
    return true;
}


// add a thread to an ARG
bool assert_trees(LocalTrees *trees)
{
    LocalTree *last_tree = NULL;
    int seqlen = 0;

    // assert first tree has null mapping and spr
    if (trees->begin() != trees->end()) {
        assert(trees->begin()->spr.is_null());
        assert(!trees->begin()->mapping);
    }

    // loop through blocks
    for (LocalTrees::iterator it=trees->begin(); it != trees->end(); ++it) {
        LocalTree *tree = it->tree;
        Spr *spr = &it->spr;
        int *mapping = it->mapping;
        seqlen += it->blocklen;
        
        assert(it->blocklen >= 0);
        assert(assert_tree(tree));

        if (last_tree) {
            if (spr->is_null()) {
                // just check that mapping is 1-to-1
                bool mapped[tree->nnodes];
                fill(mapped, mapped + tree->nnodes, false);

                for (int i=0; i<tree->nnodes; i++) {
                    assert(mapping[i] != -1);
                    assert(!mapped[mapping[i]]);
                    mapped[mapping[i]] = true;
                }

            } else {
                assert(assert_spr(last_tree, tree, spr, mapping));
            }
        }

        last_tree = tree;
    }

    assert(seqlen == trees->length());
    
    return true;
}




//=============================================================================
// C inferface
extern "C" {


LocalTrees *arghmm_new_trees(
    int **ptrees, int **ages, int **sprs, int *blocklens,
    int ntrees, int nnodes)
{
    // setup model, local trees, sequences
    return  new LocalTrees(ptrees, ages, sprs, blocklens, ntrees, nnodes);
}


int get_local_trees_ntrees(LocalTrees *trees)
{
    return trees->trees.size();
}


int get_local_trees_nnodes(LocalTrees *trees)
{
    return trees->nnodes;
}


void get_local_trees_ptrees(LocalTrees *trees, int **ptrees, int **ages,
                            int **sprs, int *blocklens)
{
    // setup permutation
    const int nleaves = trees->get_num_leaves();
    int perm[trees->nnodes];
    for (int i=0; i<nleaves; i++) 
        perm[i] = trees->seqids[i];
    for (int i=nleaves; i<trees->nnodes; i++) 
        perm[i] = i;

    // debug
    assert_trees(trees);

    // convert trees
    int i = 0;
    for (LocalTrees::iterator it=trees->begin(); it!=trees->end(); ++it, i++) {
        LocalTree *tree = it->tree;
        
        for (int j=0; j<tree->nnodes; j++) {
            int parent = tree->nodes[j].parent;
            if (parent != -1)
                parent = perm[parent];
            ptrees[i][perm[j]] = parent;
            ages[i][perm[j]] = tree->nodes[j].age;
        }
        blocklens[i] = it->blocklen;

        if (!it->spr.is_null()) {
            sprs[i][0] = perm[it->spr.recomb_node];
            sprs[i][1] = it->spr.recomb_time;
            sprs[i][2] = perm[it->spr.coal_node];
            sprs[i][3] = it->spr.coal_time;
            
            assert(it->spr.recomb_time >= ages[i-1][sprs[i][0]]);
            assert(it->spr.coal_time >= ages[i-1][sprs[i][2]]);
            
        } else {
            sprs[i][0] = it->spr.recomb_node;
            sprs[i][1] = it->spr.recomb_time;
            sprs[i][2] = it->spr.coal_node;
            sprs[i][3] = it->spr.coal_time;
        }

    }
}


void get_local_trees_ptrees2(LocalTrees *trees, int **ptrees, int **ages,
                             int **sprs, int *blocklens)
{
    // setup permutation
    const int nleaves = trees->get_num_leaves();
    int perm[trees->nnodes];
    for (int i=0; i<nleaves; i++) 
        perm[i] = trees->seqids[i];
    for (int i=nleaves; i<trees->nnodes; i++) 
        perm[i] = i;

    // debug
    assert_trees(trees);

    // convert trees
    int i = 0;
    for (LocalTrees::iterator it=trees->begin(); it!=trees->end(); ++it, i++) {
        LocalTree *tree = it->tree;
        
        for (int j=0; j<tree->nnodes; j++) {
            int parent = tree->nodes[j].parent;
            if (parent != -1)
                parent = perm[parent];
            ptrees[i][perm[j]] = parent;
            ages[i][perm[j]] = tree->nodes[j].age;
        }
        blocklens[i] = it->blocklen;

        if (!it->spr.is_null()) {
            sprs[i][0] = perm[it->spr.recomb_node];
            sprs[i][1] = it->spr.recomb_time;
            sprs[i][2] = perm[it->spr.coal_node];
            sprs[i][3] = it->spr.coal_time;
            
            assert(it->spr.recomb_time >= ages[i-1][sprs[i][0]]);
            assert(it->spr.coal_time >= ages[i-1][sprs[i][2]]);
            
        } else {
            sprs[i][0] = it->spr.recomb_node;
            sprs[i][1] = it->spr.recomb_time;
            sprs[i][2] = it->spr.coal_node;
            sprs[i][3] = it->spr.coal_time;
        }

    }
}


void delete_local_trees(LocalTrees *trees)
{
    delete trees;
}


} // extern C


} // namespace arghmm


//=============================================================================
// transitions

#ifndef ARGHMM_TRANS_H
#define ARGHMM_TRANS_H

#include "local_tree.h"
#include "model.h"
#include "states.h"

namespace arghmm {

// A compressed representation of the transition matrix
class TransMatrix
{
public:
    TransMatrix(int ntimes, int nstates, bool alloc=true) :
        ntimes(ntimes),
        nstates(nstates),
        own_data(false)
    {
        if (alloc)
            allocate(ntimes);
    }

    ~TransMatrix()
    {
        if (own_data) {
            delete [] B;
            delete [] D;
            delete [] E;
            delete [] G;
            delete [] norecombs;
            //delete [] sums;
        }
    }

    void allocate(int ntimes)
    {
        ntimes = ntimes;
        own_data = true;
        B = new double [ntimes];
        D = new double [ntimes];
        E = new double [ntimes];
        G = new double [ntimes];
        norecombs = new double [ntimes];
        //sums = new double [nstates];
    }

    // convert to log?
    inline double get_transition_prob(
        const LocalTree *tree, const States &states, int i, int j) const
    {
        const int node1 = states[i].node;
        const int a = states[i].time;
        const int c = tree->nodes[node1].age;
        const int node2 = states[j].node;
        const int b = states[j].time;
        const double I = float(a <= b);
            
        if (node1 != node2)
            return log(D[a] * E[b] * (B[min(a,b)] - I * G[a]));
        else {
            double p = D[a] * E[b] * (2*B[min(a,b)] - 2*I*G[a] - B[min(c,b)]);
            if (a == b)
                p += norecombs[a];
            return log(p);
        }
    }


    int ntimes;
    int nstates;
    bool own_data;
    double *B;
    double *D;
    double *E;
    double *G;
    double *norecombs;
    //double *sums;
};


// A compressed representation of the switch transition matrix
class TransMatrixSwitch
{
public:
    TransMatrixSwitch(int nstates1, int nstates2, bool alloc=true) :
        nstates1(nstates1),
        nstates2(nstates2),
        own_data(false)
    {
        if (alloc)
            allocate(nstates1, nstates2);
    }

    ~TransMatrixSwitch()
    {
        if (own_data) {
            delete [] determ;
            delete [] determprob;
            delete [] recoalrow;
            delete [] recombrow;
        }
    }

    void allocate(int nstates1, int nstates2)
    {
        nstates1 = nstates1;
        nstates2 = nstates2;
        own_data = true;
        determ = new int [nstates1];
        determprob = new double [nstates1];
        recoalrow = new double [nstates2];
        recombrow = new double [nstates2];
    }
    
    inline double get_transition_prob(int i, int j) const
    {
        if (i == recoalsrc) {
            return recoalrow[j];
        } else if (i == recombsrc) {
            return recombrow[j];
        } else {
            if (determ[i] == j)
                return determprob[i];
            else
                return -INFINITY;
        }
    }

    int nstates1;
    int nstates2;
    int recoalsrc;
    int recombsrc;
    bool own_data;
    int *determ;
    double *determprob;
    double *recoalrow;
    double *recombrow;
};


void calc_transition_probs(const LocalTree *tree, const ArgModel *model,
    const States &states, const LineageCounts *lineages, TransMatrix *matrix);

void calc_transition_probs(const LocalTree *tree, const ArgModel *model,
                           const States &states, const LineageCounts *lineages,
                           double **transprob);

void calc_transition_probs(const LocalTree *tree, const ArgModel *model,
                           const States &states, const LineageCounts *lineages,
                           const TransMatrix *matrix, double **transprob);

void calc_transition_probs_switch(
    const LocalTree *tree, const LocalTree *last_tree, 
    const Spr &spr, const int *mapping,
    const States &states1, const States &states2,
    const ArgModel *model, const LineageCounts *lineages, 
    TransMatrixSwitch *transmat_switch);

void calc_transition_probs_switch(
    const LocalTree *tree, const LocalTree *last_tree, 
    const Spr &spr, const int *mapping,
    const States &states1, const States &states2,
    const ArgModel *model, const LineageCounts *lineages, double **transprob);

void calc_transition_probs_switch(const TransMatrixSwitch *matrix, 
                                  double **transprob);

void calc_state_priors(const States &states, const LineageCounts *lineages, 
                       const ArgModel *model, double *priors);

void get_deterministic_transitions(
    const LocalTree *tree, const LocalTree *last_tree, 
    const Spr &spr, const int *mapping,
    const States &states1, const States &states2,
    int ntimes, int *next_states);


} // namespace arghmm

#endif // ARGHMM_TRANS_H
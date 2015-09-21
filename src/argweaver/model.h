//=============================================================================
// ArgHmm model


#ifndef ARGWEAVER_MODEL_H
#define ARGWEAVER_MODEL_H

// c/c++ includes
#include <math.h>
#include <set>
#include <map>
#include <list>

// arghmm includes
#include "track.h"

namespace argweaver {


// Returns a discretized time point
inline double get_time_point(int i, int ntimes, double maxtime, double delta=10)
{
    return (exp(i/double(ntimes) * log(1.0 + delta * maxtime)) - 1) / delta;
}

// Returns a list of discretized time points
inline void get_time_points(int ntimes, double maxtime,
                            double *times, double delta=.01)
{
    for (int i=0; i<ntimes; i++)
        times[i] = get_time_point(i, ntimes-1, maxtime, delta);
}


void get_coal_time_steps(const double *times, int ntimes,
                         double *coal_time_steps, bool linear,
                         double delta);



// The model parameters and time discretization scheme
class ArgModel
{
public:
    ArgModel(int ntimes=0, double rho=0, double mu=0) :
        owned(true),
        ntimes(ntimes),
        times(NULL),
        time_steps(NULL),
        coal_time_steps(NULL),
        popsizes(NULL),
        rho(rho),
        mu(mu),
	infsites_penalty(1.0),
        unphased(0),
        sample_phase(0),
        unphased_file("")
    {}

    // Model with constant population sizes and log-spaced time points
    ArgModel(int ntimes, double maxtime, double popsize,
             double rho, double mu) :
        owned(true),
        ntimes(ntimes),
        times(NULL),
        time_steps(NULL),
        coal_time_steps(NULL),
        popsizes(NULL),
        rho(rho),
        mu(mu),
        infsites_penalty(1.0),
        unphased(0),
        sample_phase(0)
    {
        set_log_times(maxtime, ntimes);
        set_popsizes(popsize, ntimes);
    }

    // Model with variable population sizes and log-space time points
    ArgModel(int ntimes, double maxtime, double *_popsizes,
             double rho, double mu) :
        owned(true),
        ntimes(ntimes),
        times(NULL),
        time_steps(NULL),
        coal_time_steps(NULL),
        popsizes(NULL),
        rho(rho),
        mu(mu),
        infsites_penalty(1.0),
	unphased(0),
	sample_phase(0)
    {
        set_log_times(maxtime, ntimes);
        if (_popsizes)
            set_popsizes(_popsizes, ntimes);
    }


    // Model with custom time points and variable population sizes
    ArgModel(int ntimes, double *_times, double *_popsizes,
             double rho, double mu) :
        owned(true),
        ntimes(ntimes),
        times(NULL),
        time_steps(NULL),
        coal_time_steps(NULL),
        popsizes(NULL),
        rho(rho),
        mu(mu),
        infsites_penalty(1.0),
        unphased(0),
	sample_phase(0)
    {
        set_times(_times, ntimes);
        if (_popsizes)
            set_popsizes(_popsizes, ntimes);
    }


    // share data reference
    ArgModel(const ArgModel &other, double rho, double mu) :
        owned(false),
        ntimes(other.ntimes),
        times(other.times),
        time_steps(other.time_steps),
        coal_time_steps(other.coal_time_steps),
        popsizes(other.popsizes),
        rho(rho),
        mu(mu),
	infsites_penalty(other.infsites_penalty),
        unphased(other.unphased),
	sample_phase(other.sample_phase),
        unphased_file(other.unphased_file)
    {}


    // Copy constructor
    ArgModel(const ArgModel &other) :
        ntimes(other.ntimes),
        times(NULL),
        time_steps(NULL),
        coal_time_steps(NULL),
        popsizes(NULL),
        rho(other.rho),
        mu(other.mu),
        infsites_penalty(other.infsites_penalty),
	unphased(other.unphased),
        sample_phase(other.sample_phase),
        unphased_file(other.unphased_file)
    {
        copy(other);
    }


    ~ArgModel()
    {
        clear();
    }


    // deallocate all data
    void clear() {
        if (owned) {
            delete [] times;
            delete [] time_steps;
            delete [] coal_time_steps;
            if (popsizes)
                delete [] popsizes;
        }
    }

protected:
    void clear_array(double **array) {
        if (owned && *array)
            delete [] *array;
        *array = NULL;
    }


public:
    // Copy parameters from another model
    void copy(const ArgModel &other)
    {
        owned = true;
        rho = other.rho;
        mu = other.mu;
        infsites_penalty = other.infsites_penalty;
        unphased = other.unphased;
	sample_phase = other.sample_phase;
	unphased_file = other.unphased_file;

        // copy popsizes and times
        set_times(other.times, other.coal_time_steps, ntimes);
        if (other.popsizes)
            set_popsizes(other.popsizes, ntimes);

        // copy maps
        if (other.mutmap.size() > 0)
            mutmap.insert(mutmap.begin(),
                          other.mutmap.begin(), other.mutmap.end());
        if (other.recombmap.size() > 0)
            recombmap.insert(recombmap.begin(),
                             other.recombmap.begin(), other.recombmap.end());
    }

    // Returns dummy time used for root of tree with internal branch removed
    int get_removed_root_time() const {
        return ntimes + 1;
    }

    double get_mintime() const {
        return times[1] * .1;
    }

    //=====================================================================
    // setting time points and population sizes

    // Sets the model time points from an array
    void set_times(double *_times, double *_coal_time_steps, int _ntimes) {
        ntimes = _ntimes;
        clear_array(&times);
        times = new double [ntimes];
        std::copy(_times, _times + ntimes, times);
        setup_time_steps(false, 0, _coal_time_steps);
    }


    // Sets the model time points from an array
    void set_times(double *_times, int _ntimes) {
        ntimes = _ntimes;
        clear_array(&times);
        times = new double [ntimes];
        std::copy(_times, _times + ntimes, times);
        setup_time_steps();
    }

    // Sets the model time points linearily in log space
    void set_log_times(double maxtime, int _ntimes,
		       double delta=0.01) {
        ntimes = _ntimes;
        clear_array(&times);
        times = new double [ntimes];
        get_time_points(ntimes, maxtime, times, delta);
        setup_time_steps(false, delta);
    }

    // Sets the model time points linearily
    void set_linear_times(double time_step, int _ntimes) {
        ntimes = _ntimes;
        clear_array(&times);
        times = new double [ntimes];
        for (int i=0; i<ntimes; i++)
            times[i] = i * time_step;
        setup_time_steps(true);
    }

    // Sets the model population sizes from an array
    void set_popsizes(double *_popsizes, int _ntimes) {
        ntimes = _ntimes;
        clear_array(&popsizes);
        popsizes = new double [ntimes];
        std::copy(_popsizes, _popsizes + ntimes, popsizes);
    }

    void set_popsizes(string popsize_str, int _ntimes) {
        ntimes = _ntimes;
        clear_array(&popsizes);
        popsizes = new double [ntimes];
        vector<string> tokens;
        split(popsize_str.c_str(), ",", tokens);
        if (tokens.size() == 1) {
            fill(popsizes, popsizes + ntimes, atof(tokens[0].c_str()));
        } else {
            if ((int)tokens.size() != ntimes) {
                printError("Number of popsizes (%i) does not match ntimes"
                           " (%i)\n", tokens.size(), ntimes);
                exit(1);
            }
            for (unsigned int i=0; i < tokens.size(); i++) {
                popsizes[i] = atof(tokens[i].c_str());
            }
        }
    }

    // Sets the model populations to be constant over all time points
    void set_popsizes(double popsize, int _ntimes) {
        ntimes = _ntimes;
        clear_array(&popsizes);
        popsizes = new double [ntimes];
        fill(popsizes, popsizes + ntimes, popsize);
    }

    //====================================================================
    // maps

    // Returns true if mutation map is present
    bool has_mutmap() const {
        return mutmap.size() > 0;
    }

    // Returns true if recombination map is present
    bool has_recombmap() const {
        return recombmap.size() > 0;
    }

    // Initializes mutation and recombination maps for use
    bool setup_maps(string chrom, int start, int end);

    // set model parameters from map position
    void set_map_pos(int pos) {
        mu = mutmap.find(pos, mu);
        rho = recombmap.find(pos, rho);
    }

    // Returns a model customized for the local position
    void get_local_model(int pos, ArgModel &model,
                         int *mu_idx=NULL, int *rho_idx=NULL) const {
        model.mu = mutmap.find(pos, mu, mu_idx);
        model.rho = recombmap.find(pos, rho, rho_idx);
        model.infsites_penalty = infsites_penalty;

        model.owned = false;
        model.times = times;
        model.ntimes = ntimes;
        model.time_steps = time_steps;
        model.coal_time_steps = coal_time_steps;
        model.popsizes = popsizes;
    }

    double get_local_rho(int pos, int *rho_idx=NULL) const {
        return recombmap.find(pos, rho, rho_idx);
    }

    void get_local_model_index(int index, ArgModel &model) const {
        if (mutmap.size() == 0 || recombmap.size() == 0) {
            model.mu = mu;
            model.rho = rho;
        } else {
            model.mu = mutmap[index].value;
            model.rho = recombmap[index].value;
        }
        model.infsites_penalty = infsites_penalty;
        model.unphased = unphased;
	model.sample_phase = sample_phase;
	model.unphased_file = unphased_file;

        model.owned = false;
        model.times = times;
        model.ntimes = ntimes;
        model.time_steps = time_steps;
        model.coal_time_steps = coal_time_steps;
        model.popsizes = popsizes;
    }

protected:

    // Setup time steps between time points
    // if linear=true, ignore delta and set mid-points halfway between
    //   each time step
    void setup_time_steps(bool linear=false, double delta=0.01, double *_coal_time_steps=NULL)
    {
        clear_array(&time_steps);
        time_steps = new double [ntimes];
        for (int i=0; i<ntimes-1; i++)
            time_steps[i] = times[i+1] - times[i];
        time_steps[ntimes-1] = INFINITY;

        clear_array(&coal_time_steps);
        coal_time_steps = new double [2*ntimes];
	if (_coal_time_steps == NULL)
	    get_coal_time_steps(times, ntimes, coal_time_steps, linear, delta);
	else std::copy(_coal_time_steps, _coal_time_steps + 2*ntimes, coal_time_steps);
    }

public:
    bool owned; // if true, this object owns the array pointers

    // time points (presented in generations)
    int ntimes;
    double *times;
    double *time_steps;
    double *coal_time_steps;

    // parameters
    double *popsizes;        // population sizes
    double rho;              // recombination rate (recombs/generation/site)
    double mu;               // mutation rate (mutations/generation/site)
    double infsites_penalty; // penalty for violating infinite sites
    bool unphased;
    int sample_phase;
    string unphased_file;
    Track<double> mutmap;    // mutation map
    Track<double> recombmap; // recombination map
};




} // namespace argweaver


#endif // ARGWEAVER_MODEL_H

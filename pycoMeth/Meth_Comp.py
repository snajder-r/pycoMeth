# -*- coding: utf-8 -*-

#~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~IMPORTS~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~#
# Standard library imports
from collections import OrderedDict, namedtuple, Counter
import gzip
import itertools

# Third party imports
from tqdm import tqdm
import numpy as np
import pandas as pd
from scipy.stats import kruskal, mannwhitneyu
from statsmodels.stats.multitest import multipletests

# Local imports
from pycoMeth.common import *
from pycoMeth.FileParser import FileParser
from pycoMeth.CoordGen import CoordGen

#~~~~~~~~~~~~~~~~~~~~~~~~CpG_Comp MAIN CLASS~~~~~~~~~~~~~~~~~~~~~~~~#

def Meth_Comp (
    aggregate_fn_list:[str],
    ref_fasta_fn:str,
    output_tsv_fn:str=None,
    output_bed_fn:str=None,
    max_missing:int=0,
    min_diff_llr:float=2,
    sample_id_list:[str]=None,
    pvalue_adj_method:str="fdr_bh",
    pvalue_threshold:float=0.01,
    only_tested_sites:bool=False,
    verbose:bool=False,
    quiet:bool=False,
    progress:bool=False,
    **kwargs):
    """
    Compare methylation values for each CpG positions or intervals between n samples and perform a statistical test to evaluate if the positions are
    significantly different. For 2 samples a Mann_Withney test is performed otherwise multiples samples are compared with a Kruskal Wallis test.
    pValues are adjusted for multiple tests using the Benjamini & Hochberg procedure for controlling the false discovery rate.
    * aggregate_fn_list
        A list of output tsv files corresponding to several samples to compare generated by either CpG_Aggregate or Interval_Aggregate. (can be gzipped)
    * ref_fasta_fn
        Reference file used for alignment in Fasta format (ideally already indexed with samtools faidx)
    * output_bed_fn
        Path to write a summary result file in BED format (At least 1 output file is required) (can be gzipped)
    * output_tsv_fn
        Path to write an more extensive result report in TSV format (At least 1 output file is required) (can be gzipped)
    * max_missing
        Max number of missing samples to perform the test
    * min_diff_llr
        Minimal llr boundary for negative and positive median llr.
        The test if only performed if at least one sample has a median llr above (methylated) and 1 sample has a median llr below (unmethylated)
    * sample_id_list
        list of sample ids to annotate results in tsv file
    * pvalue_adj_method
        Method to use for pValue multiple test adjustment
    * pvalue_threshold
        Alpha parameter (family-wise error rate) for pValue adjustment
    * only_tested_sites
        Do not include sites that were not tested because of insufficient samples or effect size in the report
    """

    # Init method
    opt_summary_dict = opt_summary(local_opt=locals())
    log = get_logger (name="pycoMeth_CpG_Comp", verbose=verbose, quiet=quiet)

    log.warning("Checking options and input files")
    log_dict(opt_summary_dict, log.debug, "Options summary")

    # Init collections
    coordgen = CoordGen(ref_fasta_fn, verbose, quiet)
    log_list(coordgen, log.debug, "Coordinate reference summary")

    # At least one output file is required, otherwise it doesn't make any sense
    log.debug ("Checking required output")
    if not output_bed_fn and not output_tsv_fn:
        raise pycoMethError ("At least 1 output file is requires (-t or -b)")

    # Automatically define tests and maximal missing samples depending on number of files to compare
    all_samples = len(aggregate_fn_list)
    min_samples = all_samples-max_missing

    # 3 values = Kruskal Wallis test
    if all_samples >= 3:
        pvalue_method = "KW"
        log.debug("Multiple comparison mode (Kruskal_Wallis test)")
        if min_samples < 3:
            log.debug("Automatically raise number of minimal samples to 3")
            min_samples = 3
    # 2 values = Mann_Withney test
    elif all_samples == 2:
        pvalue_method = "MW"
        log.debug("Pairwise comparison mode (Mann_Withney test)")
        if min_samples:
            log.debug("No missing samples allowed for 2 samples comparison")
            min_samples = 2
    else:
        raise pycoMethError ("Meth_Comp needs at least 2 input files")

    log.warning("Parsing files")
    try:
        log.info("Reading input files header and checking consistancy between headers")
        colnames = set()
        input_type = ""
        fp_list = []
        all_fp_len = 0

        if not sample_id_list or len(sample_id_list) != len(aggregate_fn_list):
            sample_id_list = list(range(len(aggregate_fn_list)))

        for label, fn in zip(sample_id_list, aggregate_fn_list):
            fp = FileParser(
                fn=fn,
                label=label,
                dtypes={"start":int,"end":int,"median_llr":float},
                verbose=verbose, quiet=quiet,
                include_byte_len=True)
            all_fp_len+=len(fp)
            fp_list.append(fp)

            # Check colnames
            if not colnames:
                colnames = set(fp.colnames)
            elif colnames != set(fp.colnames):
                raise ValueError (f"Invalid field {fp.colnames} in file {fn}")

            # Get input file type
            if not input_type:
                input_type = fp.input_type
            elif input_type != fp.input_type:
                raise ValueError (f"Inconsistent input types")

        # Check that aggregate_fn_list contains valid input types
        if not input_type in ["CpG_Aggregate", "Interval_Aggregate"]:
            raise pycoMethError("Invalid input file type passed (aggregate_fn_list). Expecting pycoMeth CpG_Aggregate or Interval_Aggregate output TSV files")

        # Define StatsResults to collect valid sites and perform stats
        stats_results = StatsResults(
            pvalue_method = pvalue_method,
            pvalue_adj_method = pvalue_adj_method,
            pvalue_threshold = pvalue_threshold,
            min_diff_llr = min_diff_llr,
            min_samples = min_samples,
            input_type=input_type,
            only_tested_sites=only_tested_sites)

        log.info("Starting asynchronous file parsing")
        with tqdm (total=all_fp_len, unit=" bytes", unit_scale=True, desc="\tProgress", disable=not progress) as pbar:

            coord_d = defaultdict(list)

            # Read first line from each file
            log.debug("Reading first lines")
            for fp in fp_list:
                # Move pointer up and index by coordinate
                try:
                    line = fp.next()
                    coord = coordgen(line.chromosome, line.start, line.end)
                    coord_d[coord].append(fp)
                    pbar.update(line.byte_len)
                except StopIteration:
                    raise pycoMethError ("Empty file found")

            # Continue reading lines from all files
            log.debug("Starting deep parsing")
            fp_done = 0
            while True:
                # Get lower coord if has enough samples
                lower_coord = sorted(coord_d.keys())[0]
                coord_fp_list = sorted(coord_d[lower_coord], key=lambda x: x.label)

                # Deal with lower coordinates and compute result if needed
                stats_results.compute_pvalue(
                    coord=lower_coord,
                    line_list=[coord_fp.current() for coord_fp in coord_fp_list],
                    label_list=[coord_fp.label for coord_fp in coord_fp_list])

                # Remove lower entry and move fp to next sequence
                del(coord_d[lower_coord])
                for fp in coord_fp_list:

                    # Move pointer up and index by coordinate
                    try:
                        line = fp.next()
                        coord = coordgen(line.chromosome, line.start, line.end)
                        coord_d[coord].append(fp)
                        pbar.update(line.byte_len)
                    except StopIteration:
                        fp_done+=1

                # Exit condition = all file are finished
                if fp_done == len(fp_list):
                    break

        # Init file writter
        with Comp_Writer(bed_fn=output_bed_fn, tsv_fn=output_tsv_fn, input_type=input_type, verbose=verbose) as writer:

            # Exit condition
            if not stats_results.res_list:
                log.info("No valid p-Value could be computed")

            else:
                # Convert results to dataframe and correct pvalues for multiple tests
                log.info("Adjust pvalues")
                stats_results.multitest_adjust()

                # Write output file
                log.info("Writing output file")
                for res in tqdm(stats_results.res_list, unit=" sites", unit_scale=True, desc="\tProgress", disable=not progress):
                    writer.write (res)

    finally:
        # Print counters
        log_dict(stats_results.counter, log.info, "Results summary")

        # Close input and output files
        for fp in fp_list:
            try:
                fp.close()
            except:
                pass

#~~~~~~~~~~~~~~~~~~~~~~~~~~~StatsResults HELPER CLASS~~~~~~~~~~~~~~~~~~~~~~~~~~~#

class StatsResults():
    def __init__ (self,
        pvalue_method="Kruskal",
        pvalue_adj_method="fdr_bh",
        pvalue_threshold=0.01,
        min_diff_llr=1,
        min_samples=3,
        input_type="Interval_Aggregate",
        only_tested_sites=False):
        """"""
        # Save self variables
        self.pvalue_method = pvalue_method
        self.pvalue_adj_method = pvalue_adj_method
        self.pvalue_threshold = pvalue_threshold
        self.min_diff_llr = min_diff_llr
        self.min_samples = min_samples
        self.input_type = input_type
        self.only_tested_sites = only_tested_sites

        # Init self collections
        self.res_list = []
        self.counter = Counter()

        # Get minimal non-zero float value
        self.min_pval = np.nextafter(float(0), float(1))

    #~~~~~~~~~~~~~~MAGIC AND PROPERTY METHODS~~~~~~~~~~~~~~#

    def __repr__(self):
        return dict_to_str(self.counter)

    def __len__(self):
        return len(self.res_list)

    def __iter__(self):
        for i in self.res_list:
            yield i

    #~~~~~~~~~~~~~~PUBLIC METHODS~~~~~~~~~~~~~~#

    def compute_pvalue (self, coord, line_list, label_list):
        """"""

        # Collect median llr
        med_llr_list = []
        raw_llr_list = []
        raw_pos_list = []
        n_samples = len(line_list)

        # Evaluate median llr value
        neg_med = pos_med = ambiguous_med = 0
        for line in line_list:
            if line.median_llr <= -self.min_diff_llr:
                neg_med+=1
            elif line.median_llr >= self.min_diff_llr:
                pos_med+= 1
            else:
                ambiguous_med+=1

        # Not enough samples
        if n_samples < self.min_samples:
            comment="Insufficient samples"
            pvalue=np.nan

        # Sufficient samples and effect size
        elif not neg_med or not pos_med:
            comment="Insufficient effect size"
            pvalue=np.nan

        # Sufficient samples and effect size
        else:
            comment="Valid"
            # collect med_llr, raw_llr and raw_pos lists
            for line in line_list:
                med_llr_list.append(line.median_llr)
                raw_llr_list.append(str_to_list(line.llr_list))
                if self.input_type == "Interval_Aggregate":
                    raw_pos_list.append(str_to_list(line.pos_list))

            # Run stat test
            if self.pvalue_method == "KW":
                statistics, pvalue = kruskal(*raw_llr_list)
            elif self.pvalue_method == "MW":
                statistics, pvalue = mannwhitneyu(raw_llr_list[0], raw_llr_list[1], alternative='two-sided')

            # Fix and categorize p-values
            if pvalue is np.nan or pvalue is None or pvalue>1 or pvalue<0:
                pvalue = 1.0
                self.counter["Sites with invalid pvalue"]+=1

            # Correct very low pvalues to minimal float size
            elif pvalue == 0:
                pvalue = self.min_pval

        # Update counters result table
        self.counter[comment]+=1

        # filter out non tested site is required
        if self.only_tested_sites and pvalue is np.nan:
            return

        res = OrderedDict()
        res["chromosome"] = coord.chr_name
        res["start"] = coord.start
        res["end"] = coord.end
        res["pvalue"] = pvalue
        res["adj_pvalue"] = np.nan
        res["n_samples"] = n_samples
        res["neg_med"] = neg_med
        res["pos_med"] = pos_med
        res["ambiguous_med"] = ambiguous_med
        res["label_list"] = label_list
        res["med_llr_list"] = med_llr_list
        res["raw_llr_list"] = raw_llr_list
        res["comment"] = comment
        if self.input_type == "Interval_Aggregate":
            res["raw_pos_list"] = raw_pos_list
            res["unique_cpg_pos"] = len(set(itertools.chain.from_iterable(raw_pos_list)))

        self.res_list.append(res)

    def multitest_adjust(self):
        """"""
        # Collect non-nan pvalues
        pvalue_idx = []
        pvalue_list = []
        for i, res in enumerate(self.res_list):
            if not res["pvalue"] is np.nan:
                pvalue_idx.append(i)
                pvalue_list.append(res["pvalue"])

        # Adjust values
        if pvalue_list:
            adj_pvalue_list = multipletests(
                pvals = pvalue_list,
                alpha = self.pvalue_threshold,
                method = self.pvalue_adj_method)[1]

            # add adjusted values to appropriate category
            for i, adj_pvalue in zip(pvalue_idx, adj_pvalue_list):

                # Fix and categorize p-values
                if adj_pvalue is np.nan or adj_pvalue is None or adj_pvalue>1 or adj_pvalue<0:
                    adj_pvalue = 1.0
                    comment="Non-significant pvalue"

                elif adj_pvalue <= self.pvalue_threshold:
                    # Correct very low pvalues to minimal float size
                    if adj_pvalue == 0:
                        adj_pvalue = self.min_pval
                    # update counter if pval is still significant after adjustment
                    comment = "Significant pvalue"
                else:
                    comment= "Non-significant pvalue"

                # update counters and update comment and adj pavalue
                self.counter[comment]+=1
                self.res_list[i]["comment"] = comment
                self.res_list[i]["adj_pvalue"] = adj_pvalue

#~~~~~~~~~~~~~~~~~~~~~~~~~~~Comp_Writer HELPER CLASS~~~~~~~~~~~~~~~~~~~~~~~~~~~#
class Comp_Writer():
    """Extract data for valid sites and write to BED and/or TSV file"""

    def __init__ (self,
        bed_fn=None,
        tsv_fn=None,
        input_type="Interval_Aggregate",
        verbose=True):
        """"""
        self.log = get_logger (name="Comp_Writer", verbose=verbose)
        self.bed_fn = bed_fn
        self.tsv_fn = tsv_fn
        self.input_type = input_type

        # Init file pointers
        self.bed_fp = self._init_bed () if bed_fn else None
        self.tsv_fp = self._init_tsv () if tsv_fn else None

        # Color score table
        self.colors = OrderedDict()
        self.colors[10]='10,7,35'
        self.colors[9]='32,12,74'
        self.colors[8]='60,9,101'
        self.colors[7]='87,15,109'
        self.colors[6]='112,25,110'
        self.colors[5]='137,34,105'
        self.colors[4]='163,43,97'
        self.colors[3]='187,55,84'
        self.colors[2]='209,70,67'
        self.colors[1]='230,230,230'
        self.colors[0]='230,230,230'

    #~~~~~~~~~~~~~~PUBLIC METHODS~~~~~~~~~~~~~~#
    def write (self, res):
        """"""
        if self.bed_fn:
            self._write_bed (res)
        if self.tsv_fn:
            self._write_tsv (res)

    def __enter__ (self):
        self.log.debug("Opening Writer")
        return self

    def __exit__(self, exception_type, exception_val, trace):
        self.log.debug("Closing Writer")
        for fp in (self.bed_fp, self.tsv_fp):
            try:
                fp.close()
            except:
                pass

    #~~~~~~~~~~~~~~PRIVATE METHODS~~~~~~~~~~~~~~#
    def _init_bed (self):
        """Open BED file and write file header"""
        self.log.debug("Initialise output bed file")
        mkbasedir (self.bed_fn, exist_ok=True)
        fp = gzip.open(self.bed_fn, "wt") if self.bed_fn.endswith(".gz") else open(self.bed_fn, "w")
        # Write header line
        fp.write("track name=meth_comp itemRgb=On\n")
        return fp

    def _write_bed (self, res):
        """Write line to BED file"""
        # Log transform pvalue and cast to int
        if res["adj_pvalue"] is np.nan:
            score = 0
        else:
            score = int(-np.log10(res["adj_pvalue"]))
        # Define color for bed file
        color = self.colors.get(score, self.colors[10])
        # Write line
        res_line =  [res["chromosome"], res["start"], res["end"], ".", score, ".", res["start"], res["end"], color]
        self.bed_fp.write(str_join(res_line, sep="\t", line_end="\n"))

    def _init_tsv (self):
        """Open TSV file and write file header"""
        self.log.debug("Initialise output tsv file")
        mkbasedir (self.tsv_fn, exist_ok=True)
        fp = gzip.open(self.tsv_fn, "wt") if self.tsv_fn.endswith(".gz") else open(self.tsv_fn, "w")
        # Write header line
        if self.input_type == "Interval_Aggregate":
            header = [
                "chromosome","start","end","n_samples","pvalue","adj_pvalue","neg_med","pos_med",
                "ambiguous_med","unique_cpg_pos","labels","med_llr_list","raw_llr_list","raw_pos_list", "comment"]
        elif self.input_type == "CpG_Aggregate":
            header = [
                "chromosome","start","end","n_samples","pvalue","adj_pvalue","neg_med","pos_med",
                "ambiguous_med","labels","med_llr_list","raw_llr_list", "comment"]
        fp.write(str_join(header, sep="\t", line_end="\n"))
        return fp

    def _write_tsv (self, res):
        """Write line to TSV file"""

        if self.input_type == "Interval_Aggregate":
            res_line = [
                res["chromosome"], res["start"], res["end"], res["n_samples"], res["pvalue"], res["adj_pvalue"], res["neg_med"], res["pos_med"],
                res["ambiguous_med"], res["unique_cpg_pos"], list_to_str(res["label_list"]), list_to_str(res["med_llr_list"]), list_to_str(res["raw_llr_list"]),
                list_to_str(res["raw_pos_list"]), res["comment"]]
        elif self.input_type == "CpG_Aggregate":
            res_line = [
                res["chromosome"], res["start"], res["end"], res["n_samples"], res["pvalue"], res["adj_pvalue"], res["neg_med"], res["pos_med"], res["ambiguous_med"],
                list_to_str(res["label_list"]), list_to_str(res["med_llr_list"]), list_to_str(res["raw_llr_list"]), res["comment"]]
        self.tsv_fp.write(str_join(res_line, sep="\t", line_end="\n"))

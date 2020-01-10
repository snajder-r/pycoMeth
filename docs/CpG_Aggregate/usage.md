# CpG_Aggregate usage

**Calculate methylation frequency at genomic CpG sites from the output of `nanopolish call-methylation`**

## Example usage

* [Python API usage](https://a-slide.github.io/pycoMeth/CpG_Aggregate/API_usage/)
* [Shell CLI usage](https://a-slide.github.io/pycoMeth/CpG_Aggregate/CLI_usage/)

## Input files

### Nanopolish call-methylation output file

[`Nanopolish call-methylation`](https://nanopolish.readthedocs.io/en/latest/quickstart_call_methylation.html) tsv output file or a list of files or a regex matching several files.

### Reference FASTA file

FASTA reference file used for read alignment and Nanopolish. This file is required and used to sort the CpG sites by coordinates 

## Output format

CpG_Aggregate can generates 2 files, a standard BED file and a tabulated file containing extra information

### Tabulated TSV file

This tabulated file contains the following fields:

* chromosome / start / end / strand: Genomic coordinates of the CpG or group of CpGs if in less than 5 bases from each other.
* sequence: -5 to +5 sequence of the motif or group of motifs in case split_group was not selected.
* num_motifs: Number of motif found in the group.
* median_llr: Median of log likelihood ratios for all read mapped
* llr_list: List of raw llr values

### BED file

Standard genomic [BED6](https://genome.ucsc.edu/FAQ/FAQformat.html#format1). The score correspond to the median log likelyhood ratio.
The file is already sorted by coordinates and can be rendered with a genome browser such as IGV

The sites are color-coded as follow:

* Red: Median log likelyhood ratio >= 3 (more methylated)
* Orange: Median log likelyhood ratio >= 2 (more methylated)
* Blue: Median log likelyhood ratio <= -2 (more unmethylated)
* Light blue: Median log likelyhood ratio <= -3 (more methylated)
* Grey: Median log likelyhood ration between -2 and 2 (ambiguous methylation status)

Here is an example of multiple methylation bed files rendered with IGV

![Example Bed Files](../pictures/CpG_Aggregate.png)
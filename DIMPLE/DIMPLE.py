"""
DIMPLE: Deep Indel Missense Programmable Library Engineering

Python 3.7 package for generating oligo fragments and respective primers for scanning Indel/Missense mutations

Written By: David Nedrud

Requires installation of Biopython
Simple installation command: pip install biopython

File input must be .fasta/.fa format and must include the whole plasmid for primer specificity and binding
File output will also be .fasta format

Genes with variable sections can be aligned to save library space (avoid synthesizing the same sequence multiple times)
Use align_genevariation()
"""

import itertools
import os
import re
import warnings
from difflib import SequenceMatcher
from math import ceil
from DIMPLE.utilities import findORF

import numpy as np
from Bio import SeqIO, Align
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from Bio.SeqUtils import MeltingTemp as mt
from Bio.SeqUtils import seq3


def addgene(genefile, start=None, end=None):
    """Generate a list of DIMPLE classes from a fasta file containing genes."""
    if start is None:
        start = []
    if end is None:
        end = []
    tmpgene = list(SeqIO.parse(genefile.replace("\\", ""), "fasta"))
    tmpgene[0].seq = tmpgene[0].seq.upper()
    tmpOLS = []
    for gene in tmpgene:
        if "start:" in gene.description and "end:" in gene.description:
            start = int(gene.description.split("start:")[1].split(" ")[0]) - 1
            end = int(gene.description.split("end:")[1].split(" ")[0])
            gene.filename = genefile.replace("\\", "")
            tmpOLS.append(DIMPLE(gene, start, end))
        else:
            gene.filename = genefile.replace("\\", "")
            tmpOLS.append(DIMPLE(gene, start, end))
    return tmpOLS  # only return the class object itself if one gene is given


class DIMPLE:
    """Class for generating indel mutagenic scanning libraries."""

    # Calculate and update maxfrag - Max number of nucleotides that a fragment can carry
    @property
    def synth_len(self):
        return self.__breaksites

    @synth_len.setter
    def synth_len(self, value):
        self._synth_len = value
        self.maxfrag = value - 62

    # Shared variables for all genes
    minfrag = 24  # Picked based on smallest size for golden gate fragment efficiency
    primerBuffer = 30  # This extends the sequence beyond the ORF for a primer. Must be greater than 30
    allhangF = []
    allhangR = []
    primerTm = (56.5, 60)  # Melting temperature limits for primers
    gene_primerTm = (58, 62)  # Help gene primer amplification

    # Load Barcodes
    dataDirectory = os.path.abspath(os.path.dirname(__file__))
    try:
        barcodeF = list(
            SeqIO.parse(dataDirectory + "/data/forward_finalprimers.fasta", "fasta")
        )
        barcodeR = list(
            SeqIO.parse(dataDirectory + "/data/reverse_finalprimers.fasta", "fasta")
        )
    except FileNotFoundError as exc:
        raise ValueError(
            "Could not find barcode files. Please upload your own or place standard barcodes in the data file."
        ) from exc

    def __init__(self, gene, start=None, end=None):
        # Set up random seed
        try:
            self.random_seed
        except AttributeError:
            self.random_seed = None

        # Set up random number generator
        self.rng = np.random.default_rng(self.random_seed)

        #  Search for ORF
        try:
            DIMPLE.maxfrag  # if DIMPLE.maxfrag doesn't exist, create it
        except AttributeError:
            DIMPLE.maxfrag = (
                self.synth_len - 62
            )  # based on space for barcodes, cut sites, handle. Doesn't need to be exact
        if start is None:
            start = []
        if end is None:
            end = []
        self.geneid = gene.name
        self.linked = set()
        self.genePrimer = []
        self.oligos = []
        self.barPrimer = []
        self.fullGene = gene.seq
        self.split = 0
        self.num_frag_per_oligo = 1
        self.doublefrag = 0
        self.filename = gene.filename
        self.redundantSeq = []
        # Set up variables. Could have this as user input in the class
        self.SynonymousCodons = {
            "Cys": ["TGT", "TGC"],
            "Asp": ["GAT", "GAC"],
            "Ser": ["TCT", "TCG", "TCA", "TCC", "AGC", "AGT"],
            "Gln": ["CAA", "CAG"],
            "Met": ["ATG"],
            "Asn": ["AAC", "AAT"],
            "Pro": ["CCT", "CCG", "CCA", "CCC"],
            "Lys": ["AAG", "AAA"],
            "STOP": ["TAG", "TGA", "TAA"],
            "Thr": ["ACC", "ACA", "ACG", "ACT"],
            "Phe": ["TTT", "TTC"],
            "Ala": ["GCA", "GCC", "GCG", "GCT"],
            "Gly": ["GGT", "GGG", "GGA", "GGC"],
            "Ile": ["ATC", "ATA", "ATT"],
            "Leu": ["TTA", "TTG", "CTC", "CTT", "CTG", "CTA"],
            "His": ["CAT", "CAC"],
            "Arg": ["CGA", "CGC", "CGG", "CGT", "AGG", "AGA"],
            "Trp": ["TGG"],
            "Val": ["GTA", "GTC", "GTG", "GTT"],
            "Glu": ["GAG", "GAA"],
            "Tyr": ["TAT", "TAC"],
        }
        self.aminoacids = [
            "Cys",
            "Asp",
            "Ser",
            "Gln",
            "Met",
            "Asn",
            "Pro",
            "Lys",
            "Thr",
            "Phe",
            "Ala",
            "Gly",
            "Ile",
            "Leu",
            "His",
            "Arg",
            "Trp",
            "Val",
            "Glu",
            "Tyr"
        ]
        if self.stop_codon:
            self.aminoacids.append("STOP")
        self.complement = {"A": "T", "C": "G", "G": "C", "T": "A"}

        # First check for unwanted cutsites (BsaI sites and BsmBI sites)
        match_sites = [
                gene.seq.upper().count(cut)
                + gene.seq.upper().count(cut.reverse_complement())
                for cut in DIMPLE.avoid_sequence
            ]
        if any(match_sites):
            raise ValueError(
                "Unwanted Restriction cut sites found. Please input plasmids with these removed."
                + str([DIMPLE.avoid_sequence[i] for i, x in enumerate(match_sites) if bool(x)])
            )  # change codon
        if start and end and (end - start) % 3 != 0:
            print("Gene length is not divisible by 3. Reseting start and end.")
            start = []
            end = []
        if not start and not end:
            start, end = findORF(gene)
        self.aacount = int((end - start) / 3)
        self.start = start
        self.end = end
        # record sequence with extra bp to account for primer. for plasmids (circular) we can rearrange linear sequence)
        if start - self.primerBuffer < 0:
            self.seq = (
                gene.seq[start + 3 - self.primerBuffer:]
                + gene.seq[: end + self.primerBuffer]
            )
        elif end + self.primerBuffer > len(gene.seq):
            self.seq = (
                gene.seq[start + 3 - self.primerBuffer:]
                + gene.seq[: end + self.primerBuffer - len(gene.seq)]
            )
        else:
            self.seq = gene.seq[start + 3 - self.primerBuffer: end + self.primerBuffer]
        # Determine Fragment Size and store beginning and end of each fragment
        num = int(
            round(((end - start - 3) / float(DIMPLE.maxfrag)) + 0.499999999)
        )  # total bins needed (rounded up)
        insertionsites = range(start + 3, end, 3)
        fragsize = [len(insertionsites[i::num]) * 3 for i in list(range(num))]
        # if any(x<144 for x in fragsize):
        #     raise ValueError('Fragment size too low')
        print("Initial Fragment Sizes for:" + self.geneid)
        print(fragsize)
        total = DIMPLE.primerBuffer
        breaksites = [DIMPLE.primerBuffer]
        for x in fragsize:
            total += x
            breaksites.extend([total])
        self.breaklist = [
            [x, x + fragsize[idx]] for idx, x in enumerate(breaksites[:-1])
        ]  # insertion site to insertion site
        self.problemsites = set()
        self.unique_Frag = [True] * len(fragsize)
        self.fragsize = fragsize
        self.__breaksites = breaksites
        if DIMPLE.phaseshift:
            self.phase = [0,1,2]
        else:
            self.phase = [0]

    def ochre(self):
        if len(self.SynonymousCodons["STOP"]) < 2:
            raise Exception("You have removed all stop codons")
        self.usage_ecoli["TAG"] = 1
        self.usage_human["TAG"] = 1
        # SynonymousCodons['STOP'] = ['TGA','TAA']
        del self.SynonymousCodons["STOP"][0]
        self.SynonymousCodons["OCHRE"] = ["TAG"]
        self.aminoacids.extend("OCHRE")

    def amber(self):
        if len(self.SynonymousCodons["STOP"]) < 2:
            raise Exception("You have removed all stop codons")
        self.usage_ecoli["TAA"] = 1
        self.usage_human["TAA"] = 1
        del self.SynonymousCodons["STOP"][2]
        self.SynonymousCodons["AMBER"] = ["TAA"]
        self.aminoacids.extend("AMBER")

    def opal(self):
        if len(self.SynonymousCodons["STOP"]) < 2:
            raise Exception("You have removed all stop codons")
        self.usage_ecoli["TGA"] = 1
        self.usage_human["TGA"] = 1
        del self.SynonymousCodons["STOP"][1]
        self.SynonymousCodons["OPAL"] = ["TGA"]
        self.aminoacids.extend("OPAL")

    def __getitem__(self):
        return

    # Update Breaksites
    @property
    def breaksites(self):
        return self.__breaksites

    @breaksites.setter
    def breaksites(self, value):
        if isinstance(value, list):
            if any([(x - DIMPLE.primerBuffer) % 3 != 0 for x in value]):
                raise ValueError("New Breaksites are not divisible by 3")
            if (
                value[0] != self.breaksites[0] or value[-1] != self.breaksites[-1]
            ) and not DIMPLE.dms:
                if (
                    input(
                        "Beginning and End of gene have changed. Are you sure you want to continue? (y/n)"
                    )
                    != "y"
                ):
                    raise Exception("Canceled user set break sites")
            self.__breaksites = value
            fragsize = [j - i for i, j in zip(value[:-1], value[1:])]
            self.fragsize = fragsize
            self.breaklist = [
                [x, x + fragsize[idx]] for idx, x in enumerate(value[:-1])
            ]  # insertion site to insertion site
            print("New Fragment Sizes for: " + self.geneid)
            print(fragsize)
            # fragment_genes(self)

        else:
            raise ValueError("Breaklist input is not a list")


# This function is not used in the current version of the code
# This will find genes that share the same sequence and avoid synthesizing the same oligos multiple times
def align_genevariation(OLS):
    if not isinstance(OLS[0], DIMPLE):
        raise TypeError("Not an instance of the DIMPLE class")
    match = []
    aligner = Align.PairwiseAligner()
    aligner.mode = "global"
    aligner.match_score = 2
    aligner.mismatch_score = -1
    print("------------Finding homologous regions------------")
    # First find genes with matching sequences
    for m in range(len(OLS)):
        remlist = range(len(OLS))[m + 1 :]
        for p in remlist:
            alignments = aligner.align(
                OLS[m].seq, OLS[p].seq
            )
            for alignment in alignments:
                score = alignment.score
                score_len = alignment.indices.shape[1]
                break
            if score / score_len > 1.5:  # Threshold for a matched gene set
                index = [
                    x for x, geneset in enumerate(match) if m in geneset or p in geneset
                ]  # Determine if aligned genes are in any of the previously matched sets
                if not index:  # Create a new set if not
                    print(OLS[m].geneid)
                    print(OLS[p].geneid)
                    if input("Are these genes linked? (y/n):") == "y":
                        match.append(set([m, p]))
                else:
                    if p not in match[index[0]] or m not in match[index[0]]:
                        for items in match[index[0]].union(set([p, m])):
                            print(OLS[items].geneid)
                        if input("Are these genes linked? (y/n):") == "y":
                            match[index[0]].add(p)
                            match[index[0]].add(m)
    # Create fragments for each match
    if match:
        for tmpset in match:
            matchset = list(tmpset)
            print(
                "Determining Gene Variation for genes:"
                + ",".join([OLS[i].geneid for i in matchset])
            )
            max_gene_len = 0
            variablesites = set()
            for i, j in itertools.combinations(matchset, 2):
                max_gene_len = max(
                    max_gene_len,
                    len(OLS[i].seq) - 2 * DIMPLE.primerBuffer,
                    len(OLS[j].seq) - 2 * DIMPLE.primerBuffer,
                )
                seq_match = SequenceMatcher(None, OLS[i].seq, OLS[j].seq)
                # Determine variable regions
                variablesites.update(
                    [
                        x.size
                        for x in seq_match.get_matching_blocks()
                        if x.size != len(OLS[i].seq)
                        and x.size != len(OLS[j].seq)
                        and x.size != 0
                    ]
                )  # not sure how to account for zero
            problemsites = set()
            for kk in variablesites:
                problemsites.update(
                    range(kk - DIMPLE.primerBuffer, kk + DIMPLE.primerBuffer)
                )  # Add space for primers to bind
            # Determine Fragment Size while avoiding variable regions - must be same for all genes
            num = int(
                round(((max_gene_len) / float(DIMPLE.maxfrag)) + 0.499999999)
            )  # total bins needed (rounded up)
            insertionsites = range(
                DIMPLE.primerBuffer, max_gene_len + DIMPLE.primerBuffer - 6, 3
            )  # all genes start with a buffer
            fragsize = [len(insertionsites[i::num]) * 3 for i in list(range(num))]
            total = DIMPLE.primerBuffer
            breaksites = [
                DIMPLE.primerBuffer
            ]  # first site is always the max primer length (adjusted at beginning)
            for x in fragsize:
                total += x
                breaksites.extend([total])
            available_sites = [
                xsite
                for xsite in range(0, max_gene_len + DIMPLE.primerBuffer + 1, 3)
                if xsite not in problemsites
            ]
            breaksites = [
                site
                if site in available_sites
                else min(available_sites, key=lambda x: abs(x - site))
                for site in breaksites
            ]  # remove problemsites?
            if any(x < DIMPLE.minfrag or x > DIMPLE.maxfrag for x in fragsize):
                print(fragsize)
                raise ValueError(
                    "Fragment size too low"
                )  # this was decided by author. could be changed
            fragsize = [j - i for i, j in zip(breaksites[:-1], breaksites[1:])]
            breaklist = [
                [x, x + fragsize[idx]] for idx, x in enumerate(breaksites[:-1])
            ]  # insertion site to insertion site
            unique_Frag = [
                [] for x in range(max(matchset) + 1)
            ]  # a list of fragments that do not match
            for x in breaklist:
                sequences = [str(OLS[i].seq[x[0] : x[1]]) for i in matchset]
                index = [
                    matchset[i]
                    for i, x in enumerate(sequences)
                    if i == sequences.index(x)
                ]
                for idx in matchset:
                    if idx in index:
                        unique_Frag[idx].extend([True])
                    else:
                        unique_Frag[idx].extend([False])

            print("Finished Alignment. Fragment Sizes for combined genes:")
            print(fragsize)
            for (
                idx
            ) in (
                matchset
            ):  # setting these to the same variable should link them for processing later
                OLS[
                    idx
                ].problemsites = problemsites  # add gap range to problemsites variable to avoid breaking in a gap
                OLS[idx].breaklist = breaklist
                OLS[idx].fragsize = fragsize
                OLS[idx].breaksites = breaksites
                OLS[idx].linked.update(matchset)
                OLS[idx].unique_Frag = unique_Frag[idx]
    else:
        print(
            "No redundant sequences found. Matching sequences may be too short or not aligned to reduce number of oligos synthesized"
        )


def find_geneprimer(genefrag, start, end):
    # 3' end of primer is variable to adjust melting temperature
    # 5' end of primer is fixed, with restriction site added
    primer = (
        genefrag[start:end].complement() + DIMPLE.cutsite[::-1] + "ATA"
    )  # added ATA for cleavage close to end of DNA fragment
    # Check melting temperature
    # find complementary sequences
    comp = 0  # compensate for bases that align with bsmbi
    while primer.complement()[end - start + comp] == genefrag[end + comp]:
        comp += 1
    # comp += 1 # This is important for single basepair overhang
    tm2 = mt.Tm_NN(primer[0: end - start + comp], nn_table=mt.DNA_NN2)
    tm4 = mt.Tm_NN(primer[0: end - start + comp], nn_table=mt.DNA_NN4)
    count = 0
    while (
        tm2 < DIMPLE.gene_primerTm[0]
        or tm2 > DIMPLE.gene_primerTm[1]
        or tm4 < DIMPLE.gene_primerTm[0]
        or tm4 > DIMPLE.gene_primerTm[1]
    ):
        if tm2 < DIMPLE.gene_primerTm[0] or tm4 < DIMPLE.gene_primerTm[0]:
            start += -1
            primer = (
                genefrag[start:end].complement() + DIMPLE.cutsite[::-1] + "ATA"
            )  # cut site addition
            tm2 = mt.Tm_NN(primer[0: end - start + comp], nn_table=mt.DNA_NN2)
            tm4 = mt.Tm_NN(primer[0: end - start + comp], nn_table=mt.DNA_NN4)
        if (
            count > 12 or start == 0
        ):  # stop if caught in inf loop or if linker is at max (31 + 7 = 38 bases)
            break
        if tm2 > DIMPLE.gene_primerTm[1] and tm4 > DIMPLE.gene_primerTm[1]:
            start += 1
            primer = genefrag[start:end].complement() + DIMPLE.cutsite[::-1] + "ATA"
            # tm = mt.Tm_NN(primer[0:e-s+comp],c_seq=genefrag[s:e+comp],nn_table=mt.DNA_NN2)
            tm2 = mt.Tm_NN(primer[0: end - start + comp], nn_table=mt.DNA_NN2)
            tm4 = mt.Tm_NN(primer[0: end - start + comp], nn_table=mt.DNA_NN4)
        count += 1
    # optional - force first nucleotide to a C or G
    # while primer[0]=="T" or primer[0]=="A" or primer[0]=="t" or primer[0]=="a":
    #     s += -1
    #     primer = genefrag[s:e].complement()+"CTCTGCA"
    #     tm = mt.Tm_NN(primer[0:e-s+comp],nn_table=mt.DNA_NN2)
    # return final primer with tm
    print(
        "Generated primers: ",
        primer.complement().reverse_complement(),
        round(tm2, 1),
        start,
    )
    return primer.complement().reverse_complement(), round(tm2, 1), start


def find_fragment_primer(fragment, stop):
    # This function finds optimal primer for OLS subpool by changing 3' end of primer
    start = 0  # starts at maximum length (5' is fixed)
    if stop > 25:  # limit primer to 25 bases to begin with
        end = 25
    else:
        end = stop
    count = 0
    primer = fragment[start:end]
    tm2 = mt.Tm_NN(
        primer, nn_table=mt.DNA_NN2
    )  # Two methods of finding melting temperature seems more consistent
    tm4 = mt.Tm_NN(primer, nn_table=mt.DNA_NN4)
    while (
        tm2 < DIMPLE.primerTm[0]
        or tm2 > DIMPLE.primerTm[1]
        or tm4 < DIMPLE.primerTm[0]
        or tm4 > DIMPLE.primerTm[1]
        or len(primer) < 16
    ):
        count += 1
        if (
            count > 12 or end > stop
        ):  # stop if caught in inf loop or if primer is larger than the barcode
            end = stop
            primer = fragment[start:end]
            break

        if tm2 < DIMPLE.primerTm[0] or tm4 < DIMPLE.primerTm[0]:
            if start == 0:
                break
            end += 1
            primer = fragment[start:end]
            tm2 = mt.Tm_NN(primer, nn_table=mt.DNA_NN2)
            tm4 = mt.Tm_NN(primer, nn_table=mt.DNA_NN4)

        if tm2 > DIMPLE.primerTm[1] or tm4 > DIMPLE.primerTm[1]:
            end += -1
            primer = fragment[start:end]
            tm2 = mt.Tm_NN(primer, nn_table=mt.DNA_NN2)
            tm4 = mt.Tm_NN(primer, nn_table=mt.DNA_NN4)
    return primer, round(tm2, 1)


def check_nonspecific(primer, fragment, point):
    non = []
    # fragment is the entire gene sequence plus the buffer sequence on each side
    # point is the position of the primer in the fragment
    # Forward
    for i in range(len(fragment) - len(primer)):  # Scan each position
        # first check if the primer binds at each position in the fragment
        match = [
            primer[j].lower() == fragment[i + j].lower() for j in range(len(primer))
        ]
        first = 10
        for k in range(len(match) - 3):
            if (match[k] and match[k + 1] and match[k + 3]) or (
                match[k] and match[k + 1] and match[k + 2]
            ):
                first = k
                break
        # if the primer binds to 80% of the first ... bases
        # and more than 6 bases
        # and the 3' matches
        # then check melting temperature
        if (
            sum(match[first:]) > len(primer[first:]) * 0.8
            and sum(match[first:]) > 6
            and match[-1]
            and point != i
        ):  # string compare - sum of matched nt is greater than 80%
            try:
                # check the melting temperature of the primer
                melt = mt.Tm_NN(
                    primer[first:],
                    c_seq=fragment[i + first : i + len(primer)].complement(),
                    nn_table=mt.DNA_NN2,
                    de_table=mt.DNA_DE1,
                    imm_table=mt.DNA_IMM1,
                )
                if melt > 25:
                    print("Found non-specific match at " + str(i + 1) + "bp:")
                    print("match: " + fragment[i: i + len(primer)])
                    print("primer:" + primer + " Tm:" + str(round(melt, 1)))
                if melt > 35:
                    non.append(True)
            except ValueError as valerr:
                print(
                    str(valerr)
                    + ". Please check position manually:"
                    + str(i + 1)
                    + " forward"
                )
                print("Primer:" + primer)
                print("Match: " + fragment[i : i + len(primer)])
                non.append(False)
    # Reverse
    fragment = fragment.reverse_complement()
    for i in range(len(fragment) - len(primer)):
        match = [
            primer[j].lower() == fragment[i + j].lower() for j in range(len(primer))
        ]
        first = 10
        for k in range(0, len(match) - 3, 1):
            if match[k] and match[k + 1] and match[k + 3]:
                first = k
                break
        if (
            sum(match[first:]) > len(primer[first:]) * 0.8
            and sum(match[first:]) > 6
            and match[-1]
            and point != -i
        ):  # string compare - sum of matched nt is greater than 80%
            try:
                melt = mt.Tm_NN(
                    primer[first:],
                    c_seq=fragment[i + first : i + len(primer)].complement(),
                    nn_table=mt.DNA_NN2,
                    de_table=mt.DNA_DE1,
                    imm_table=mt.DNA_IMM1,
                )
                if melt > 20:
                    print("Found non-specific match at " + str(i + 1) + "bp:")
                    print(" match:" + fragment[i : i + len(primer)])
                    print("primer:" + primer + " Tm:" + str(melt))
                if melt > 35:
                    non.append(True)
            except ValueError as valerr:
                print(
                    str(valerr)
                    + ". Please check position manually:"
                    + str(i + 1)
                    + " reverse"
                )
                print("Primer:" + primer)
                print("Match: " + fragment[i : i + len(primer)])
                non.append(False)
    return sum(non)


def recalculate_num_fragments(gene):
    num = int(
        round(((gene.end - gene.start) / float(gene.maxfrag)) + 0.499999999)
    )  # total bins needed (rounded up)
    insertionsites = range(gene.start + 3, gene.end, 3)
    gene.fragsize = [len(insertionsites[i::num]) * 3 for i in list(range(num))]
    total = DIMPLE.primerBuffer
    breaksites = [DIMPLE.primerBuffer]
    for x in gene.fragsize:
        total += x
        breaksites.extend([total])
    # if DIMPLE.dms:
    #     tmpbreaklist = []
    #     for idx, x in enumerate(breaksites[:-1]):
    #         if idx:
    #             tmpbreaklist.append([x, x + gene.fragsize[idx]])
    #         else:
    #             tmpbreaklist.append([x + 3, x + gene.fragsize[idx] + 3])
    #     gene.breaklist = tmpbreaklist
    # else:
    gene.breaklist = [
        [x, x + gene.fragsize[idx]] for idx, x in enumerate(breaksites[:-1])
    ]  # insertion site to insertion site
    #gene.problemsites = set()
    gene.breaksites = breaksites
    gene.unique_Frag = [True] * len(gene.fragsize)
    return gene


def switch_fragmentsize(gene, detectedsite, OLS):
    """TODO:
    Docstring
    """

    if not isinstance(gene, DIMPLE):
        raise TypeError("Not an instance of the DIMPLE class")
    skip = False
    count = 0
    count2 = 0
    print("Non specific Fragment:" + str(detectedsite))
    if (
        len(gene.fragsize) * gene.maxfrag < len(gene.seq) - gene.primerBuffer * 2
    ):  # if the maxfrag has changed and it is impossible to split the gene into x number of fragments it should recalculate the number of fragments
        gene = recalculate_num_fragments(gene)
    else:
        gene.problemsites.add(gene.breaksites[detectedsite])
    if all(item == gene.maxfrag for item in gene.fragsize) or any(
        item > gene.maxfrag for item in gene.fragsize
    ):
        gene.maxfrag += -1
    while True:
        if count > len(gene.breaksites):
            # Randomly shift a fragment
            count = 0
            count2 += 1
            detectedsite = gene.rng.integers(
                1, len(gene.breaksites) - 1, dtype=int
            )  # dont change beginning or end
            if gene.fragsize[detectedsite - 1] == gene.maxfrag:
                shift = -3
            else:
                if gene.rng.integers(0, 2, dtype=int):
                    shift = 3
                else:
                    shift = -3
            gene.breaksites[detectedsite] = gene.breaksites[detectedsite] + shift
            gene.fragsize = [
                j - i for i, j in zip(gene.breaksites[:-1], gene.breaksites[1:])
            ]
            # if DIMPLE.dms:
            #     tmpbreaklist = []
            #     for idx, x in enumerate(gene.breaksites[:-1]):
            #         if idx:
            #             tmpbreaklist.append([x, x + gene.fragsize[idx]])
            #         else:
            #             tmpbreaklist.append([x + 3, x + gene.fragsize[idx] + 3])
            #     gene.breaklist = tmpbreaklist
            # else:
            gene.breaklist = [
                [x, x + gene.fragsize[idx]]
                for idx, x in enumerate(gene.breaksites[:-1])
            ]
            if count2 > len(gene.breaklist) * 3:
                gene.maxfrag += -1  # try to change for only this gene...
                if len(gene.fragsize) * gene.maxfrag < len(gene.seq):
                    gene = recalculate_num_fragments(gene)
                    count = 0
                    count2 = 0
        count += 1
        # Find connecting Fragments
        if detectedsite == 0 or detectedsite == len(gene.fragsize):
            print("Issue with primer on end of gene")
            skip = True
            break
        if (
            gene.fragsize[detectedsite] == gene.fragsize[detectedsite - 1]
            and gene.fragsize[detectedsite] >= gene.maxfrag
        ):
            if all(
                item >= gene.maxfrag for item in gene.fragsize[detectedsite + 1 :]
            ) and not all(
                item >= gene.maxfrag for item in gene.fragsize[: detectedsite - 1]
            ):
                shift = 3
                while gene.breaksites[detectedsite] + shift in gene.problemsites:
                    shift += 3
            if all(
                item >= gene.maxfrag for item in gene.fragsize[: detectedsite - 1]
            ) and not all(
                item >= gene.maxfrag for item in gene.fragsize[detectedsite + 1 :]
            ):
                shift = -3
                while gene.breaksites[detectedsite] + shift in gene.problemsites:
                    shift += -3
            else:
                if (
                    detectedsite < len(gene.fragsize) / 2
                ):  # should be based on problemsites not where it is located in the gene
                    shift = 3
                    while gene.breaksites[detectedsite] + shift in gene.problemsites:
                        shift += 3
                else:
                    shift = -3
                    while gene.breaksites[detectedsite] + shift in gene.problemsites:
                        shift += -3
        elif gene.fragsize[detectedsite] > gene.fragsize[detectedsite - 1]:
            shift = 3
            while gene.breaksites[detectedsite] + shift in gene.problemsites:
                shift += 3
        elif gene.fragsize[detectedsite] < gene.fragsize[detectedsite - 1]:
            shift = -3
            while gene.breaksites[detectedsite] + shift in gene.problemsites:
                shift += -3
        elif (
            gene.fragsize[detectedsite] == gene.fragsize[detectedsite - 1]
            and gene.fragsize[detectedsite] < gene.maxfrag
        ):
            shift = -3
            while gene.breaksites[detectedsite] + shift in gene.problemsites:
                shift = -shift
                if shift < 0:
                    shift += -3
        # Process shift and reprocess fragments
        gene.breaksites[detectedsite] = gene.breaksites[detectedsite] + shift
        gene.fragsize = [
            j - i for i, j in zip(gene.breaksites[:-1], gene.breaksites[1:])
        ]
        # if DIMPLE.dms:
        #     tmpbreaklist = []
        #     for idx, x in enumerate(gene.breaksites[:-1]):
        #         if idx:
        #             tmpbreaklist.append([x, x + gene.fragsize[idx] + 3])
        #         else:
        #             tmpbreaklist.append([x + 3, x + gene.fragsize[idx] + 3])
        #     gene.breaklist = tmpbreaklist
        # else:
        gene.breaklist = [
            [x, x + gene.fragsize[idx]]
            for idx, x in enumerate(gene.breaksites[:-1])
        ]
        # recheck for size limit issues
        tmpsite = [
            topidx for topidx, item in enumerate(gene.fragsize) if item > gene.maxfrag
        ]
        if tmpsite:
            # pick which side to adjust
            if tmpsite[0] == len(gene.fragsize):
                detectedsite = tmpsite[0]
            elif tmpsite[0] == 0:
                detectedsite = tmpsite[0] + 1
            elif tmpsite[0] == detectedsite and tmpsite[0] + 1 < len(gene.fragsize):
                detectedsite = tmpsite[0] + 1
            else:
                detectedsite = tmpsite[0]
        else:
            break
    print(gene.fragsize)
    # align all linked genes to the same breaksites
    for tmp in gene.linked:
        OLS[tmp].breaksites = gene.breaksites
        OLS[tmp].fragsize = gene.fragsize
        OLS[tmp].breaklist = gene.breaklist
    return skip


def check_overhangs(gene, OLS, overlapL, overlapR):
    """TODO:
    Docstring
    """
    # Force all overhangs to be different within a gene (no more than 2 matching in a row)
    switched = False
    if not isinstance(gene, DIMPLE):
        raise TypeError("Not an instance of the DIMPLE class")
    while True:
        detectedsites = set()  # stores matching overhangs
        for idx, y in enumerate(gene.breaklist):
            # TODO: remove hardcoded 4 to allow different overhang lengths
            overhang_F = gene.seq[
                y[0] - DIMPLE.cutsite_overhang - overlapL: y[0] - overlapR
            ]  # Forward overhang
            overhang_R = gene.seq[
                y[1] + overlapL: y[1] + DIMPLE.cutsite_overhang + overlapR
            ]  # Reverse overhang
            if (
                overhang_F == overhang_R
                or overhang_F == overhang_R.reverse_complement()
            ):
                detectedsites.update([idx])
        # overhang = []
        # for idx, y in enumerate(gene.breaklist):
        #     overhang.append([gene.seq[y[0] - DIMPLE.cutsite_overhang - overlapL: y[0] - overlapR], idx])  # Forward overhang
        #     overhang.append([gene.seq[y[1] + overlapL: y[1] + DIMPLE.cutsite_overhang + overlapR], idx + 1])  # Reverse overhang
        # detectedsites = set()  # stores matching overhangs
        # for i in range(len(overhang)):  # check each overhang for matches
        #     for j in [x for x in range(len(overhang)) if x != i]:  # permutate over every overhang combination to find matches
        #         #if overhang[i][0] == overhang[j][0] or overhang[i][0][:3] == overhang[j][0][:3] or overhang[i][0][1:] == overhang[j][0][1:] or overhang[i][0] == overhang[i][0].reverse_complement():  # no 3 matching bases
        #         if overhang[i][0] == overhang[j][0] or overhang[i][0] == overhang[i][0].reverse_complement():  # no 3 matching bases
        #             detectedsites.update([overhang[i][1]])
        for detectedsite in detectedsites:
            switched = True
            if detectedsite == 0:
                detectedsite = 1  # don't mess with the first cut site
            print(
                "------------------ Fragment size swapped due to matching overhangs ------------------"
            )
            skip = switch_fragmentsize(gene, detectedsite, OLS)
        else:  # if no detected sites
            break
    return switched

def split_to_codons(seq):
    ''' Split a sequence into codons.
    Input: Coding sequence (Seq object or string)
    Output: List of codons
    '''
    return [seq[i: i + 3] for i in range(0, len(seq), 3)]

def generate_DMS_fragments(
    OLS, overlapL, overlapR, synonymous, custom_mutations, dms=True, insert=False, duplicate=False, delete=False, dis=False, folder=""
):
    # TODO: Calculate maxfrag not just assuming 62
    """Generates the mutagenic oligos and writes the output to files."""
    # dms set to true for subsitition mutations
    # insert set to a list of insertions
    # duplicate set to a list of numbers of nt duplications
    # delete set to a list of numbers of symmetrical deletions
    if not isinstance(OLS[0], DIMPLE):
        raise TypeError("Not an instance of the DIMPLE class")
    # Loop through each gene or gene variation
    finishedGenes = []
    # Adjust fragments to account for variable sized fragments with the same subpool barcodes/primers
    if insert or duplicate or delete or dis:
        insert_list = []
        if insert:
            insert_list.extend(insert)
        if dis:
            insert_list.append(DIMPLE.handle)
        if insert or dis:
            DIMPLE.maxfrag = (
                DIMPLE.synth_len
                - 62
                - max([len(x) for x in insert_list])
                - overlapL
                - overlapR
            )  # increase barcode space to allow for variable sized fragments within an oligo
        if duplicate:
            DIMPLE.maxfrag = (
                DIMPLE.synth_len
                - 62
                - max(duplicate)
                - overlapL
                - overlapR
            )  # increase barcode space to allow for variable sized fragments within an oligo
        if delete and not insert and not dis and not duplicate:
            DIMPLE.maxfrag = DIMPLE.synth_len - 62 - overlapL - overlapR
        print("New max fragment:" + str(DIMPLE.maxfrag))
        for gene in OLS:
            switch_fragmentsize(gene, 1, OLS)
    # Generate oligos for each gene
    for ii, gene in enumerate(OLS):
        print(gene.breaklist)
        print(
            "--------------------------------- Analyzing Gene:"
            + gene.geneid
            + " ---------------------------------"
        )
        # gene.breaklist[0][0] += 0  # Do not mutate first codon
        # gene.fragsize[0] += -3  # Adjust size to match breaklist
        gene.maxfrag = DIMPLE.maxfrag
        if not any(
            [tmp in finishedGenes for tmp in gene.linked]
        ):  # only run analysis for one of the linked genes
            # Quality Control for overhangs from the same gene
            check_overhangs(gene, OLS, overlapL, overlapR)
        # Generate oligos and Primers
        idx = 0  # index for fragment
        totalcount = 0
        # storage for unused barcodes
        compileF = []
        compileR = []
        missingSites = []
        offset_list = []
        # missingTable = [[1]*gene.aacount]*gene.aacount
        missingFragments = []
        all_grouped_oligos = []
        uniquefrag_set = set()
        truncfrag_set = set()
        stop_codons = gene.SynonymousCodons["STOP"]
        # Loop through each fragment
        while idx < len(gene.breaklist):
            if idx == 0:
                gene.oligos = []
                gene.barPrimer = []
                gene.genePrimer = []
            frag = gene.breaklist[idx]
            grouped_oligos = []
            # AA range for fragment (need to subtract beginning primer buffer)
            fragstart = str(int((frag[0] - DIMPLE.primerBuffer) / 3) + 2) # jossie changed index to be one less. then changed back
            fragend = str(int((frag[1] - DIMPLE.primerBuffer) / 3) + 1) # jossie changed index to be one less. then changed back
            print(
                "Creating Fragment:"
                + gene.geneid
                + " --- Fragment #" + str(idx+1) + " AA:"
                + fragstart
                + "-"
                + fragend
            )
            # only run analysis for one of the linked genes
            if not any(
                [tmp in finishedGenes for tmp in gene.linked]
            ):
                # Primers for gene amplification with addition of restriction enzyme site
                genefrag_R = gene.seq[
                    frag[0] - DIMPLE.primerBuffer: frag[0] + DIMPLE.primerBuffer
                ]
                reverse, tmR, sR = find_geneprimer(
                    genefrag_R, 15, DIMPLE.primerBuffer + 1 - overlapL
                )  # 15 is just a starting point
                genefrag_F = gene.seq[
                    frag[1] - DIMPLE.primerBuffer: frag[1] + DIMPLE.primerBuffer
                ]
                forward, tmF, sF = find_geneprimer(
                    genefrag_F.reverse_complement(),
                    15,
                    DIMPLE.primerBuffer + 1 - overlapR
                )
                # negative numbers look for reverse primers
                # 10 bases is the buffer overhang on the primer (ATA + (N))
                tmpr = check_nonspecific(reverse, gene.seq, frag[0] - len(gene.seq) + 3 + len(DIMPLE.cutsite_buffer) + len(DIMPLE.cutsite) - overlapL)
                tmpf = check_nonspecific(forward, gene.seq, frag[1] - 3 - len(DIMPLE.cutsite_buffer) - len(DIMPLE.cutsite) + overlapR)
                if tmpf or tmpr:
                    # swap size with another fragment
                    print(
                        "------------------ Fragment size swapped due to non-specific primers ------------------"
                    )
                    if tmpf:
                        idx = idx + 1
                        print("Non specific primer F: " + forward)
                    else:
                        print("Non specific primer R: " + reverse)
                    # swap size with another fragment
                    skip = switch_fragmentsize(gene, idx, OLS)
                    if skip:
                        # if end of gene, try to extend primer to make it more specific?
                        if tmpr:
                            reverse += gene.complement[genefrag_R[sR - 1]]
                            warnings.warn(
                                "Gene primer at the end of gene has non specific annealing. Please Check this primer manually: " + str(reverse)
                            )
                        if tmpf:
                            idx -= 1
                            forward += Seq(
                                genefrag_F.reverse_complement()[sF - 1]
                            ).reverse_complement()
                            warnings.warn(
                                "Gene primer at the end of gene has non specific annealing. Please Check this primer manually: " + str(forward)
                            )
                    else:
                        # Quality Control for overhangs from the same gene
                        # check_overhangs(gene, OLS)
                        DIMPLE.barcodeF.extend(compileF)  # return unused barcodes
                        DIMPLE.barcodeR.extend(compileR)
                        compileF = []  # reset unused primers
                        compileR = []
                        gene.genePrimer = (
                            []
                        )  # reset gene all primers due to nonspecific primer
                        gene.barPrimer = []
                        idx = 0
                        continue  # return to the beginning
                elif check_overhangs(gene, OLS, overlapL, overlapR):
                    DIMPLE.barcodeF.extend(compileF)  # return unused barcodes
                    DIMPLE.barcodeR.extend(compileR)
                    compileF = []  # reset unused primers
                    compileR = []
                    gene.genePrimer = (
                        []
                    )  # reset gene all primers due to nonspecific primer
                    gene.barPrimer = []
                    idx = 0
                    continue  # return to the beginning
                # Store
                gene.genePrimer.append(
                    SeqRecord(
                        reverse,
                        id=gene.geneid + "_geneP_Mut-" + str(idx + 1) + "_R",
                        description="Frag"
                        + fragstart
                        + "-"
                        + fragend
                        + " "
                        + str(tmR)
                        + "C",
                    )
                )
                gene.genePrimer.append(
                    SeqRecord(
                        forward,
                        id=gene.geneid + "_geneP_Mut-" + str(idx + 1) + "_F",
                        description="Frag"
                        + fragstart
                        + "-"
                        + fragend
                        + " "
                        + str(tmF)
                        + "C",
                    )
                )
                # Determine missing double mutations
                beginning = int(
                    (frag[0] - DIMPLE.primerBuffer - sR) / 3
                )  # Region missing double mutations
                if beginning < 1:
                    beginning = 1
                end = ceil((frag[1] - DIMPLE.primerBuffer + sF) / 3)
                if end > ceil((gene.breaksites[-1] - DIMPLE.primerBuffer) / 3):
                    end = ceil((gene.breaksites[-1] - DIMPLE.primerBuffer) / 3)
                missingTmp = set()
                for site in range(
                    beginning, end
                ):  # Record these missing double mutations
                    for site2 in range(site + 1, end):
                        missingSites.append([site, site2])
                        missingTmp.add(site)
                        missingTmp.add(site2)
                        # missingTable[site][site2] = 0
                missingFragments.append(
                    [(frag[0] - 30) / 3, (frag[1] - 30) / 3, list(missingTmp)]
                )
            if gene.unique_Frag[idx]:  # only for unique sequences
                # Create gene fragments with insertions
                count = 0
                tmpseq = gene.seq[
                    frag[0] - DIMPLE.cutsite_overhang - overlapL : frag[1] + DIMPLE.cutsite_overhang + overlapR
                ].replace(
                    "-", ""
                )  # extract sequence for oligo fragment include an extra 4 bases for BsmBI cut site and overlap
                offset = DIMPLE.cutsite_overhang + overlapL
                ## Create the mutations
                dms_sequences = []
                dms_sequences_double = []
                # list positions to mutate
                if custom_mutations:
                    # find custom mutations in the fragment range
                    tmp_positions = list(custom_mutations.keys())
                    tmp_tmp_positions = [
                        x * 3 - 3 + gene.primerBuffer
                        for x in list(custom_mutations.keys())
                    ]
                    tmp_mut_positions = [
                        [i, x + offset - frag[0]]
                        for i, x in enumerate(tmp_tmp_positions)
                        if frag[0] <= x + 3 <= frag[1] - 3
                    ]
                    mut_positions = [x for i, x in tmp_mut_positions]
                    positions = [tmp_positions[i] for i, x in tmp_mut_positions]
                else:
                    mut_positions = range(offset, offset + frag[1] - frag[0], 3)
                    positions = [int((frag[0] + x + 3 - offset - DIMPLE.primerBuffer) / 3) for x in mut_positions]
                ### Deep Mutational Scanning
                if dms:
                    mutations = {}
                    for i in mut_positions:
                        wt_codon = tmpseq[i : i + 3].upper()
                        wt = [
                            name
                            for name, codon in gene.SynonymousCodons.items()
                            if wt_codon in codon
                        ] 
                        if custom_mutations:
                            mutations_to_make = [
                                seq3(x)
                                for x in custom_mutations[
                                    positions[mut_positions.index(i)]
                                ].split(",")
                            ]
                        else:
                            mutations_to_make = gene.aminoacids
                        for jk in (x for x in mutations_to_make): 
                            # check if synonymous and if user wants these mutations
                            if jk not in wt[0] or synonymous:
                                codons = [
                                    aa
                                    for aa in gene.SynonymousCodons[jk]
                                    if aa not in wt_codon
                                ]
                                p = [
                                    gene.usage[aa] for aa in codons 
                                ]  # Find probabilities but not wild type codon
                                p = [
                                    xp if xp > 0.1 else 0 for xp in p
                                ]  # Remove probabilities below 0.1
                                p = [xp / sum(p) for xp in p]  # Normalize to 1
                                if not p:
                                    continue
                                # if the user wants to maximize the number of nucleotide changes
                                synonymous_mutation = []
                                synonymous_position = 0
                                if DIMPLE.maximize_nucleotide_change:
                                    # remove codons with only one change compared to wt_codon
                                    max_codons = [x for x in codons if sum([x[i] != wt_codon[i] for i in range(3)]) > 1]
                                    if max_codons:
                                        # if there are codons with more than one base change
                                        mutation = gene.rng.choice(
                                            max_codons, 1, p
                                        )  # Pick one codon
                                        xfrag = (
                                                tmpseq[0:i] + mutation[0] + tmpseq[i + 3:]
                                        )  # Add mutation to fragment
                                    else:
                                        # no codons with more than one base change. Creating synonymous mutation in neighboring codon.
                                        mutation = gene.rng.choice(
                                            codons, 1, p
                                        )  # Pick one codon
                                        # find neighboring codon
                                        tmp_synonymous = [name for name, codon in gene.SynonymousCodons.items() if tmpseq[i-3:i] in codon]
                                        synonymous_codons = gene.SynonymousCodons[tmp_synonymous[0]]
                                        max_synonymous = [x for x in synonymous_codons if sum([x[c] != tmpseq[i-3:i][c] for c in range(3)]) > 0]
                                        if max_synonymous and not (idx == 0 and mut_positions.index(i) == 0):
                                            synonymous_mutation = gene.rng.choice(max_synonymous, 1)
                                            xfrag = (
                                                    tmpseq[0:i-3] + synonymous_mutation[0] + mutation[0] + tmpseq[i + 3:]
                                            )  # Add mutation to fragment
                                            synonymous_position = -1
                                        else:
                                            tmp_synonymous = [name for name, codon in gene.SynonymousCodons.items() if tmpseq[i+3:i+6] in codon]
                                            synonymous_codons = gene.SynonymousCodons[tmp_synonymous[0]]
                                            max_synonymous = [x for x in synonymous_codons if
                                                              sum([x[c] != tmpseq[i+3:i+6][c] for c in range(3)]) > 0]
                                            if max_synonymous:
                                                synonymous_mutation = gene.rng.choice(
                                                    max_synonymous, 1
                                                )
                                                xfrag = (
                                                        tmpseq[0:i] + mutation[0] + synonymous_mutation[0] + tmpseq[i+6:]
                                                )  # Add mutation to fragment
                                                synonymous_position = +1
                                            else:
                                                print('Unable to create synonymous mutation in neighboring codon. Continuing with single nucleotide change')
                                                xfrag = (tmpseq[0:i] + mutation[0] + tmpseq[i + 3:])
                                                print(xfrag)
                                else:
                                    mutation = gene.rng.choice(
                                        codons, 1, p
                                    )  # Pick one codon
                                    xfrag = (
                                            tmpseq[0:i] + mutation[0] + tmpseq[i + 3:]
                                    )  # Add mutation to fragment
                                # Check each cassette for more than 2 BsmBI and 2 BsaI sites
                                avoid_count = 0
                                while any(
                                    [
                                        (
                                            xfrag.upper().count(x)
                                            + xfrag.upper().count(
                                                x.reverse_complement()
                                            )
                                        )
                                        > 0
                                        for x in DIMPLE.avoid_sequence
                                    ]
                                ):
                                    mutation = gene.rng.choice(
                                        gene.SynonymousCodons[jk], 1, p
                                    )  # Pick one codon
                                    avoid_count += 1
                                    xfrag = tmpseq[0:i] + mutation[0] + tmpseq[i + 3 :]
                                    if avoid_count > 10:
                                        warnings.warn(
                                            "Unwanted restriction site found within fragment: " + str(xfrag)
                                        )
                                        break
                                mutations[
                                    ">"
                                    + wt[0]
                                    + str(
                                        int(
                                            (frag[0] + i + 6 - offset - DIMPLE.primerBuffer)
                                            / 3
                                        )
                                    )
                                    + jk
                                    ] = mutation[0]
                                # if there was a synonymous mutation added then add the synonymous mutation to the mutation list
                                if synonymous_mutation:
                                    mutations[
                                        ">"
                                        + wt[0]
                                        + str(
                                            int(
                                                (frag[0] + i + 6 - offset - DIMPLE.primerBuffer)
                                                / 3
                                            )
                                        )
                                        + jk
                                        ] += str(synonymous_position) + '_' + synonymous_mutation[0]
                                dms_sequences.append(
                                    SeqRecord(
                                        xfrag,
                                        id=gene.geneid
                                        + "_DMS-"
                                        + str(idx + 1)
                                        + "_"
                                        + wt[0]
                                        + str(
                                            int(
                                                (
                                                    frag[0]
                                                    + i
                                                    + 6
                                                    - offset
                                                    - DIMPLE.primerBuffer
                                                )
                                                / 3
                                            )
                                        )
                                        + jk,
                                        description="Frag " + fragstart + "-" + fragend,
                                    )
                                )
                        # if double mutations are selected then make every possible double mutation
                        if DIMPLE.make_double:
                            # select every permutation of mut_positions order doesn't matter
                            for combi in itertools.combinations(mutations.keys(), 2):
                                # extract number from mutation name
                                if "STOP" not in combi[0] and "STOP" not in combi[1]:
                                    pos1 = mut_positions[
                                        positions.index(int(re.findall(r'\d+', combi[0])[0]))
                                    ]
                                    pos2 = mut_positions[
                                        positions.index(int(re.findall(r'\d+', combi[1])[0]))
                                    ]
                                    if pos1 != pos2:
                                        xfrag = (
                                                tmpseq[0:pos1]
                                                + mutations[combi[0]]
                                                + tmpseq[pos1 + 3: pos2]
                                                + mutations[combi[1]]
                                                + tmpseq[pos2 + 3:]
                                        )
                                        dms_sequences_double.append(
                                            SeqRecord(
                                                xfrag,
                                                id=gene.geneid
                                                   + "_DMS-"
                                                   + str(idx + 1)
                                                   + "_"
                                                   + combi[0].strip(">")
                                                   + "+"
                                                   + combi[1].strip(">"),
                                                description="Frag " + fragstart + "-" + fragend
                                            )
                                        )
                    # record mutation for analysis with NGS
                    with open(
                        os.path.join(
                            folder.replace("\\", ""), gene.geneid + "_mutations.csv"
                        ),
                        "a",
                    ) as file:
                        for mut in mutations.keys():
                            file.write(mut + "\n")
                            file.write(mutations[mut] + "\n")
                
                # fix the bug where dimple doesn't insert after the last codon in the gene
                if idx == len(gene.breaklist) - 1:
                    final_codon_extension = 3
                else:
                    final_codon_extension = 0
                ### Scanning Insertions
                # add phase-shifting to insertions?
                if insert:
                    for i in range(offset, offset + frag[1] - frag[0] + final_codon_extension, 3):
                        for insert_n in insert:
                            xfrag = (
                                tmpseq[0:i] + insert_n + tmpseq[i:]
                            )  # Add mutation to fragment
                            # Check each cassette for more than 2 BsmBI and 2 BsaI sites
                            while any(
                                [
                                    (
                                        xfrag.upper().count(x)
                                        + xfrag.upper().count(x.reverse_complement())
                                    )
                                    > 0
                                    for x in DIMPLE.avoid_sequence
                                ]
                            ):
                                warnings.warn(
                                    "Unwanted restriction site found within insertion fragment: " + str(xfrag)
                                )
                                break
                                # not sure how to solve this issue
                                # mutation?
                                # xfrag = tmpseq[0:i] + mutation + tmpseq[i + 3:]
                            dms_sequences.append(
                                SeqRecord(
                                    xfrag,
                                    id=gene.geneid
                                    + "_insert-"
                                    + str(idx + 1)
                                    + "_"
                                    + insert_n
                                    + "-"
                                    + str(
                                        int(
                                            (
                                                frag[0]
                                                + i
                                                + 3
                                                - offset
                                                - DIMPLE.primerBuffer
                                            )
                                            / 3
                                        )
                                    ),
                                    description="Frag " + fragstart + "-" + fragend,
                                )
                            )
                ### Scanning Phase-variable duplications
                if duplicate:
                    for phase in gene.phase:
                        for i in range(offset, offset + frag[1] - frag[0] + final_codon_extension, 3):
                            gene_i = frag[0] + i - offset - 3 # codon index in full gene
                            dup_start = gene_i + phase
                            print(frag[0], frag[1])
                            for duplicate_n in duplicate:
                                if dup_start + duplicate_n <= gene.end: # dont duplicate past the last codon of the gene
                                    # want to slice duplication from original coding sequence to avoid going out of range at end of fragment
                                    duplication = gene.seq[dup_start : dup_start + duplicate_n].replace("-", "")
                                    if len(duplication) % 3 == 0: # don't add frameshift, which won't happen if user enters duplicate argument as multiples of 3
                                        insert_site = i + phase + duplicate_n - 3
                                        xfrag = tmpseq[0: insert_site] + duplication + tmpseq[insert_site: ]
                                        xfrag_cds = xfrag[DIMPLE.cutsite_overhang + overlapL : -(DIMPLE.cutsite_overhang + overlapR)]
                                        premature_stop = xfrag_cds.translate().count("*")
                                        if xfrag not in uniquefrag_set and not premature_stop: # Avoid adding redundant sequences and premature stops
                                            uniquefrag_set.add(xfrag)
                                            if DIMPLE.maximize_nucleotide_change and duplicate_n > 3: # recode long duplicates with synonymous mutations -- easier to synthesize
                                                recode_start, recode_end = 0, len(duplication)
                                                if phase != 0:
                                                    recode_start, recode_end = 3 - phase, -phase
                                                dup_slice = str(duplication[recode_start: recode_end])
                                                # Synonymously mutate every codon of the duplication, maximizing the number of nucleotide changes
                                                old_cds = split_to_codons(dup_slice)
                                                recode_dup_slice = ""
                                                for old_codon in old_cds:
                                                    old_codon = old_codon.upper()
                                                    old_aa = [aa for aa, codons in gene.SynonymousCodons.items() if old_codon in codons][0]
                                                    syn_codons = gene.SynonymousCodons[old_aa]                                                       
                                                    change_3 = [cod for cod in syn_codons if (sum([old_codon[i] != cod[i] for i in range(3)]) == 3)]
                                                    change_2 = [cod for cod in syn_codons if (sum([old_codon[i] != cod[i] for i in range(3)]) == 2)]
                                                    change_1 = [cod for cod in syn_codons if (sum([old_codon[i] != cod[i] for i in range(3)]) == 1)]
                                                    if change_3:
                                                        p = [gene.usage[codon] for codon in change_3]
                                                        #p = [xp if xp > 0.1 else 0 for xp in p]
                                                        p = [xp/sum(p) for xp in p]
                                                        recode_dup_slice += gene.rng.choice(change_3, 1, p)[0]
                                                    elif change_2:
                                                        p = [gene.usage[codon] for codon in change_2]
                                                        #p = [xp if xp > 0.1 else 0 for xp in p]
                                                        p = [xp/sum(p) for xp in p]
                                                        recode_dup_slice += gene.rng.choice(change_2, 1, p)[0]
                                                    elif change_1:
                                                        p = [gene.usage[codon] for codon in change_1]
                                                        #p = [xp if xp > 0.1 else 0 for xp in p]
                                                        p = [xp/sum(p) for xp in p]
                                                        recode_dup_slice += gene.rng.choice(change_1, 1, p)[0]
                                                    else:
                                                        recode_dup_slice += old_codon # could not recode (Met or Trp)
                                                recode_dup = duplication[0:recode_start] + recode_dup_slice + duplication[recode_end:len(duplication)]
                                                xfrag = tmpseq[0: insert_site] + recode_dup + tmpseq[insert_site: ]
                                                recode_dup_name = ">" + str(recode_dup)
                                            else:
                                                recode_dup_name = ""
                                            # Check each cassette for more than 2 BsmBI and 2 BsaI sites
                                            while any(
                                                [
                                                    (
                                                        xfrag.upper().count(x)
                                                        + xfrag.upper().count(x.reverse_complement())
                                                    )
                                                    > 0
                                                    for x in DIMPLE.avoid_sequence
                                                ]
                                            ):
                                                warnings.warn(
                                                    "Unwanted restriction site found within insertion fragment: " + str(xfrag)
                                                )
                                                break # can fix by recoding codons
                                            dms_sequences.append(
                                                SeqRecord(
                                                    xfrag,
                                                    id=gene.geneid
                                                    + "_dup-"
                                                    + str(idx + 1) # index of sublibrary 
                                                    + "_"
                                                    + str(duplication)
                                                    + recode_dup_name
                                                    + "-"
                                                    + str(
                                                        int(
                                                            (
                                                                frag[0]
                                                                + i
                                                                + 3
                                                                - offset
                                                                - DIMPLE.primerBuffer
                                                            )
                                                            / 3 # amino acid position
                                                        )
                                                    )
                                                    + "+"
                                                    + str(phase), # final name format: Kir_dup-1_GACATG>GATATG-33+0
                                                    description="Frag " + fragstart + "-" + fragend,
                                                )
                                            )
                                        else:
                                            if premature_stop:
                                                stop = "STOP"
                                            else:
                                                stop = ""
                                            gene.redundantSeq.append(
                                                SeqRecord(
                                                    xfrag,
                                                    id=gene.geneid
                                                    + "_dup-"
                                                    + str(idx + 1)
                                                    + "_"
                                                    + str(duplication)
                                                    + "-"
                                                    + str(
                                                        int(
                                                            (
                                                                frag[0]
                                                                + i
                                                                + 3
                                                                - offset
                                                                - DIMPLE.primerBuffer
                                                            )
                                                            / 3 
                                                        )
                                                    )
                                                    + "+"
                                                    + str(phase)
                                                    + stop,
                                                    description="Frag " + fragstart + "-" + fragend,
                                                )
                                            )
                ### Scanning Phase-variable Deletions
                if delete:
                    for phase in gene.phase:
                        for i in range(offset, offset + frag[1] - frag[0], 3):
                            for delete_n in delete:
                                if delete_n + i + phase > len(tmpseq):
                                    print("overlap: ", overlapL)
                                    print("offset: ", offset)
                                    print("frag: ", frag)
                                    print("tmpseq: ", tmpseq)
                                    print("delete_n: ", delete_n)
                                    print("length: ", len(tmpseq))
                                    print("max i: ", offset + frag[1] - frag[0] + 3)
                                    print("i: ", i)
                                    print("phase-shift: +", phase)
                                    raise ValueError(
                                        "deletions cannot be larger than fragment itself: increase overlap length."
                                    )
                                else:
                                    xfrag = (
                                        tmpseq[0:i + phase] + tmpseq[i + phase + delete_n:]
                                    )  # delete forward from position only
                                    xfrag_cds = xfrag[DIMPLE.cutsite_overhang + overlapL : -(DIMPLE.cutsite_overhang + overlapR)]
                                    premature_stop = xfrag_cds.translate().count("*")
                                    if xfrag not in uniquefrag_set and not premature_stop: # Avoid adding redundant sequences and premature stops
                                        uniquefrag_set.add(xfrag)
                                        # Check each cassette for more than 2 BsmBI and 2 BsaI sites
                                        while any(
                                            [
                                                (
                                                    xfrag.upper().count(x)
                                                    + xfrag.upper().count(x.reverse_complement())
                                                )
                                                > 0
                                                for x in DIMPLE.avoid_sequence
                                            ]
                                        ):
                                            warnings.warn(
                                                "Unwanted restriction site found within insertion fragment: " + str(xfrag)
                                            )
                                            break
                                            # xfrag = tmpseq[0:i-delete_n-3] + tmpseq[i+delete_n:] iteratively shift deletion to avoid cut sites? or mutate codons of near by aa?
                                        dms_sequences.append(
                                            SeqRecord(
                                                xfrag,
                                                id=gene.geneid
                                                + "_delete-"
                                                + str(idx + 1)
                                                + "_"
                                                + str(delete_n)
                                                + "-"
                                                + str(
                                                    int(
                                                        (
                                                            frag[0]
                                                            + i
                                                            + 6
                                                            - offset
                                                            - DIMPLE.primerBuffer
                                                        )
                                                        / 3
                                                    )
                                                )
                                                + "+"
                                                + str(phase),
                                                description="Frag " + fragstart + "-" + fragend,
                                            )
                                        )
                                    else:
                                        gene.redundantSeq.append(
                                            SeqRecord(
                                                xfrag,
                                                id=gene.geneid
                                                + "_delete-"
                                                + str(idx + 1)
                                                + "_"
                                                + str(delete_n)
                                                + "-"
                                                + str(
                                                    int(
                                                        (
                                                            frag[0]
                                                            + i
                                                            + 6
                                                            - offset
                                                            - DIMPLE.primerBuffer
                                                        )
                                                        / 3
                                                    )
                                                )
                                                + "+"
                                                + str(phase),
                                                description="Frag " + fragstart + "-" + fragend,
                                            )
                                        )
                ### Scanning Domain Insertions
                if dis:
                    # insertion
                    for i in range(offset, offset + frag[1] - frag[0], 3):
                        # if idx == 0:
                        #    continue
                        xfrag = (
                                tmpseq[0:i] + DIMPLE.handle + tmpseq[i:]
                        )  # Add mutation to fragment
                        # Check each cassette for more than 2 BsmBI and 2 BsaI sites
                        while any(
                                [
                                    (
                                            xfrag.upper().count(x)
                                            + xfrag.upper().count(x.reverse_complement())
                                    )
                                    > 2
                                    for x in DIMPLE.avoid_sequence
                                ]
                        ):
                            warnings.warn(
                                "Unwanted restriction site found within insertion fragment: " + str(xfrag)
                            )
                            # not sure how to solve this issue
                            # mutation?
                            # xfrag = tmpseq[0:i] + mutation + tmpseq[i + 3:]
                        dms_sequences.append(
                            SeqRecord(
                                xfrag,
                                id=gene.geneid
                                   + "_DIS-"
                                   + str(idx + 1)
                                   + "_"
                                   + str(
                                    int(
                                        (frag[0] + i + 3 - offset - DIMPLE.primerBuffer) / 3
                                    )
                                ),
                                description="Frag " + fragstart + "-" + fragend,
                            )
                        )
                for idx_type, dms_sequence_list in enumerate(
                        [dms_sequences, dms_sequences_double]
                ):
                    if dms_sequence_list:  # are there any sequences to write?
                        tmF = 0
                        tmR = 0
                        if gene.num_frag_per_oligo > 1:
                            dms_sequence_list = combine_fragments(
                                dms_sequence_list, gene.num_frag_per_oligo, gene.split
                            )
                        len_cutsite = len(DIMPLE.cutsite) + len(DIMPLE.cutsite_buffer) + DIMPLE.cutsite_overhang
                        # determine barcodes for subpool amplification based on smallest size
                        frag_sizes = [len(xf.seq) for xf in dms_sequence_list]
                        smallest_frag = dms_sequence_list[
                            frag_sizes.index(min(frag_sizes))
                        ].seq
                        while (
                                tmF < DIMPLE.primerTm[0] or tmR < DIMPLE.primerTm[0]
                        ):  # swap out barcode if tm is low
                            difference = DIMPLE.synth_len - (
                                    len(smallest_frag) + len_cutsite*2
                            )  # 14 bases is the length of the restriction sites with overhangs (7 bases each)
                            barF = DIMPLE.barcodeF.pop(0)
                            barR = DIMPLE.barcodeR.pop(0)
                            count += 1  # How many barcodes used
                            compileF.append(barF)
                            compileR.append(barR)
                            while difference / 2 > len(barF):
                                tmpF = DIMPLE.barcodeF.pop(0)
                                tmpR = DIMPLE.barcodeR.pop(0)
                                compileF.append(tmpF)
                                compileR.append(tmpR)
                                barF += tmpF
                                barR += tmpR
                                count += 1  # How many barcodes used
                            tmpfrag_1 = (
                                    barF.seq[0: int(difference / 2)]
                                    + DIMPLE.cutsite
                                    + DIMPLE.cutsite_buffer
                                    + tmpseq[0:DIMPLE.cutsite_overhang]
                            )  # include recognition site and the 4 base overhang
                            tmpfrag_2 = (
                                    tmpseq[-DIMPLE.cutsite_overhang:]
                                    + DIMPLE.cutsite_buffer.reverse_complement()
                                    + DIMPLE.cutsite.reverse_complement()
                                    + barR.seq.reverse_complement()[
                                      0: difference - int(difference / 2)
                                      ]
                            )
                            # primers for amplifying subpools
                            offset = (
                                    int(difference / 2) + len_cutsite
                            )  # add 11 bases for type 2 restriction
                            primerF, tmF = find_fragment_primer(tmpfrag_1, 25)
                            if len(primerF) > 21:
                                tmF = 0
                            primerR, tmR = find_fragment_primer(
                                tmpfrag_2.reverse_complement(), 25
                            )
                            if len(primerR) > 21:
                                tmR = 0
                        group_oligos = []
                        for (
                                sequence
                        ) in (
                                dms_sequence_list
                        ):  # add barcodes to the fragments to make the oligos
                            if insert or delete or duplicate:
                                difference = (
                                        DIMPLE.synth_len - len(sequence.seq[DIMPLE.cutsite_overhang:-DIMPLE.cutsite_overhang]) - len_cutsite*2
                                )  # how many bases need to be added to make oligo correct length
                                offset = int(difference / 2)  # force it to be a integer
                                combined_sequence = (
                                        tmpfrag_1[:offset]
                                        + tmpfrag_1[-len_cutsite:]
                                        + sequence.seq[DIMPLE.cutsite_overhang:-DIMPLE.cutsite_overhang]
                                        + tmpfrag_2[:len_cutsite]
                                        + tmpfrag_2[-(difference - offset):]
                                )
                            else:
                                combined_sequence = (
                                        tmpfrag_1 + sequence.seq[DIMPLE.cutsite_overhang:-DIMPLE.cutsite_overhang] + tmpfrag_2
                                )
                            if (
                                    primerF not in combined_sequence
                                    or primerR.reverse_complement() not in combined_sequence
                            ):
                                print(primerF)
                                print(combined_sequence)
                                print("---")
                                print(combined_sequence.reverse_complement())
                                print(primerR)
                                raise Exception("primers no longer bind to oligo")
                            if (
                                    combined_sequence.upper().count(DIMPLE.cutsite)
                                    + combined_sequence.upper().count(
                                    DIMPLE.cutsite.reverse_complement()
                                    )
                                    < 2
                            ):
                                raise Exception("Oligo does not have 2 cutsites")
                            if gene.doublefrag == 0:
                                gene.oligos.append(
                                    SeqRecord(
                                        combined_sequence,
                                        id=sequence.id,
                                        description="",
                                    )
                                )
                            else:
                                grouped_oligos.append(
                                    SeqRecord(
                                        combined_sequence,
                                        id=sequence.id,
                                        description="",
                                    )
                                )

                        # Store primers for gene fragment
                        if idx_type == 0:
                            gene.barPrimer.append(
                                SeqRecord(
                                    primerF,
                                    id=gene.geneid + "_oligoP_DMS-" + str(idx + 1) + "_F",
                                    description="Frag"
                                                + fragstart
                                                + "-"
                                                + fragend
                                                + "_"
                                                + str(tmF)
                                                + "C",
                                )
                            )
                            gene.barPrimer.append(
                                SeqRecord(
                                    primerR,
                                    id=gene.geneid + "_oligoP_DMS-" + str(idx + 1) + "_R",
                                    description="Frag"
                                                + fragstart
                                                + "-"
                                                + fragend
                                                + "_"
                                                + str(tmR)
                                                + "C",
                                )
                            )
                        else:
                            gene.barPrimer.append(
                                SeqRecord(
                                    primerF,
                                    id=gene.geneid + "_oligoP_DMS-double-" + str(idx + 1) + "_F",
                                    description="Frag"
                                                + fragstart
                                                + "-"
                                                + fragend
                                                + "_"
                                                + str(tmF)
                                                + "C",
                                )
                            )
                            gene.barPrimer.append(
                                SeqRecord(
                                    primerR,
                                    id=gene.geneid + "_oligoP_DMS-double-" + str(idx + 1) + "_R",
                                    description="Frag"
                                                + fragstart
                                                + "-"
                                                + fragend
                                                + "_"
                                                + str(tmR)
                                                + "C",
                                )
                            )
                        print("Barcodes tested:" + str(count))
                        # return unused barcodes
                        DIMPLE.barcodeF.extend(compileF[:-2])
                        DIMPLE.barcodeR.extend(compileR[:-2])
                        print("Barcodes Remaining:" + str(len(DIMPLE.barcodeF)))
                        compileF = []  # reset unused primers
                        compileR = []
            if gene.doublefrag == 1:
                all_grouped_oligos.append(grouped_oligos)
            idx += 1
        # Resolve Double Fragment
        if gene.doublefrag == 1:
            while len(all_grouped_oligos) > 1:
                listOne = all_grouped_oligos.pop(0)
                listTwo = all_grouped_oligos.pop(0)
                while listOne and listTwo:
                    one = listOne.pop(0)
                    two = listTwo.pop(0)
                    combined_sequence = one.seq + two.seq.reverse_complement()
                    combined_id = one.id + two.id
                    gene.oligos.append(
                        SeqRecord(combined_sequence, id=combined_id, description="")
                    )
                if listOne or listTwo:
                    if listOne:
                        sequence = listOne.pop(0)
                    if listTwo:
                        sequence = listTwo.pop(0)
                    combined_id = sequence.id
                    combined_sequence = sequence.seq
                    difference = 230 - len(combined_sequence)
                    # print(len(tmpseq))
                    barF2 = DIMPLE.barcodeF.pop(0)
                    barR2 = DIMPLE.barcodeR.pop(0)
                    while difference / 2 > len(barF2):
                        barF2 += DIMPLE.barcodeF.pop(0)
                        barR2 += DIMPLE.barcodeR.pop(0)
                    combined_sequence2 = (
                        barF2.seq[0 : int(difference / 2)]
                        + combined_sequence
                        + barR2.seq.reverse_complement()[
                            0 : difference - int(difference / 2)
                        ]
                    )
                    gene.oligos.append(
                        SeqRecord(combined_sequence2, id=combined_id, description="")
                    )
            if all_grouped_oligos:
                one = all_grouped_oligos
                while one:
                    sequence_one = one.pop(0)
                    combined_id = sequence_one.id
                    combined_sequence = sequence_one.seq
                    difference = 230 - len(combined_sequence)
                    # print(len(tmpseq))
                    barF2 = DIMPLE.barcodeF.pop(0)
                    barR2 = DIMPLE.barcodeR.pop(0)
                    while difference / 2 > len(barF2):
                        barF2 += DIMPLE.barcodeF.pop(0)
                        barR2 += DIMPLE.barcodeR.pop(0)
                    combined_sequence2 = (
                        barF2.seq[0 : int(difference / 2)]
                        + combined_sequence
                        + barR2.seq.reverse_complement()[
                            0 : difference - int(difference / 2)
                        ]
                    )
                    gene.oligos.append(
                        SeqRecord(combined_sequence2, id=combined_id, description="")
                    )
        # Fragments
        SeqIO.write(
            gene.oligos,
            os.path.join(folder.replace("\\", ""), gene.geneid + "_DMS_Oligos.fasta"),
            "fasta",
        )
        # Barcode Primers
        SeqIO.write(
            gene.barPrimer,
            os.path.join(
                folder.replace("\\", ""), gene.geneid + "_DMS_Oligo_Primers.fasta"
            ),
            "fasta",
        )
        # Amplification Primers
        SeqIO.write(
            gene.genePrimer,
            os.path.join(
                folder.replace("\\", ""), gene.geneid + "_DMS_Gene_Primers.fasta"
            ),
            "fasta",
        )
        # Redundant Sequences
        SeqIO.write(
            gene.redundantSeq,
            os.path.join(folder.replace("\\", ""), gene.geneid + "_Redundant.fasta"),
            "fasta",
        )
        # Record finished gene for aligned genes
        finishedGenes.extend([ii])


def combine_fragments(tandem, num_frag_per_oligo, split):
    """TODO:
    Docstring
    """
    tandem_seq = []
    barcodes = []
    if split:
        tmpF = DIMPLE.barcodeF.pop(0)
        tmpR = DIMPLE.barcodeR.pop(0)
    direction = -1
    while len(tandem) > num_frag_per_oligo:
        tmp = tandem.pop(0)
        tmp_tandem = tmp.seq
        tandem_id = tmp.id
        for x in range(num_frag_per_oligo - 1):
            if split:
                name = tmp.id
                tmp = tandem.pop(0)
                if direction == 1:
                    tmp_tandem += (
                        "G"
                        + DIMPLE.cutsite.reverse_complement()
                        + tmpR
                        + tmpF
                        + DIMPLE.cutsite
                        + "C"
                        + tmp.seq
                    )  # concatenate and add cut sites with buffer
                    direction = -1
                else:
                    tmp_tandem += (
                        "G"
                        + DIMPLE.cutsite.reverse_complement()
                        + tmpR
                        + tmpF
                        + DIMPLE.cutsite
                        + "C"
                        + tmp.seq.reverse_complement()
                    )  # concatenate and add cut sites with buffer
                    direction = 1
                tandem_id += "+" + tmp.id
                barcodes.append(SeqRecord(tmpR, id=name))
                barcodes.append(SeqRecord(tmpF, id=tmp.id))
            else:
                tmp = tandem.pop(0)
                if direction == 1:
                    tmp_tandem += (
                        "G"
                        + DIMPLE.cutsite.reverse_complement()
                        + "ACGT"
                        + DIMPLE.cutsite
                        + "C"
                        + tmp.seq
                    )  # concatenate and add cut sites with buffer
                    direction = -1
                else:
                    tmp_tandem += (
                        "G"
                        + DIMPLE.cutsite.reverse_complement()
                        + "ACGT"
                        + DIMPLE.cutsite
                        + "C"
                        + tmp.seq.reverse_complement()
                    )
                    direction = 1
                tandem_id += "+" + tmp.id
        tandem_seq.append(SeqRecord(tmp_tandem, id=tandem_id, description=""))

    if tandem:
        direction = -1
        print(len(tandem))
        tmp = tandem.pop(0)
        tmp_tandem = tmp.seq
        tandem_id = tmp.id
        while tandem:
            if split:
                name = tmp.id
                tmp = tandem.pop(0)
                if direction == 1:
                    tmp_tandem += (
                        "G"
                        + DIMPLE.cutsite.reverse_complement()
                        + "ACGT"
                        + DIMPLE.cutsite
                        + "C"
                        + tmp.seq
                    )  # concatenate and add cut sites with buffer
                    direction = 1
                else:
                    tmp_tandem += (
                        "G"
                        + DIMPLE.cutsite.reverse_complement()
                        + "ACGT"
                        + DIMPLE.cutsite
                        + "C"
                        + tmp.seq.reverse_complement()
                    )  # concatenate and add cut sites with buffer
                    direction = -1
                tandem_id += "+" + tmp.id
                barcodes.append(SeqRecord(tmpR, id=name))
                barcodes.append(SeqRecord(tmpF, id=tmp.id))
            else:
                tmp = tandem.pop(0)
                if direction == -1:
                    tmp_tandem += (
                        "G"
                        + DIMPLE.cutsite.reverse_complement()
                        + "ACGT"
                        + DIMPLE.cutsite
                        + "C"
                        + tmp.seq
                    )  # concatenate and add cut sites with buffer
                    direction = 1
                else:
                    tmp_tandem += (
                        "G"
                        + DIMPLE.cutsite.reverse_complement()
                        + "ACGT"
                        + DIMPLE.cutsite
                        + "C"
                        + tmp.seq
                    )
                    direction = -1
                tandem_id += "+" + tmp.id
        difference = len(tandem_seq[-1].seq) - len(tmp_tandem)
        barF = DIMPLE.barcodeF.pop(0)
        barR = DIMPLE.barcodeR.pop(0)
        while difference / 2 > len(barF):
            barF += DIMPLE.barcodeF.pop(0)
            barR += DIMPLE.barcodeR.pop(0)
        tmpfrag = (
            barF.seq[0 : int(difference / 2)]
            + tmp_tandem
            + barR.seq.reverse_complement()[0 : difference - int(difference / 2)]
        )
        tandem_seq.append(SeqRecord(tmpfrag, id=tandem_id, description=""))
        print("Partial sequence" + str(len(tmpfrag)))
    return tandem_seq


def print_all(OLS, folder=""):
    """Writes oligos and primers to files."""
    if not isinstance(OLS[0], DIMPLE):
        raise TypeError("Not an instance of the DIMPLE class")
    alloligos = []
    allprimers = []
    for obj in OLS:
        try:
            alloligos.extend(obj.oligos)
            allprimers.extend(obj.barPrimer)
            allprimers.extend(obj.genePrimer)
        except AttributeError:
            print(obj.geneid + " has not been processed")
    # Remove redundant sequences?
    SeqIO.write(
        alloligos, os.path.join(folder.replace("\\", ""), "All_Oligos.fasta"), "fasta"
    )
    SeqIO.write(
        allprimers, os.path.join(folder.replace("\\", ""), "All_Primers.fasta"), "fasta"
    )

def post_qc(OLS):
    if not isinstance(OLS[0], DIMPLE):
        raise TypeError("Not an instance of the DIMPLE class")
    # Post QC
    all_oligos = []
    all_barPrimers = []
    for obj in OLS:
        try:
            all_oligos.extend(obj.oligos)
            all_barPrimers.extend(obj.barPrimer)
        except AttributeError:
            print(obj.geneid + " has not been processed")

    print("Running QC for barcode primer specificity")
    cassetteSet = set(all_oligos[0].id[:-6])
    uCassette = [SeqRecord(all_oligos[0].seq, id=all_oligos[0].id[:-6])]
    for idx in range(len(all_oligos)):
        if not all_oligos[idx].id[:-6] in cassetteSet:
            uCassette.append(SeqRecord(all_oligos[idx].seq, id=all_oligos[idx].id[:-6]))
        cassetteSet.add(all_oligos[idx].id[:-6])
    grouped = iter(all_barPrimers)
    grouped = zip(grouped, grouped)  # create the combinatorial comparisons
    nonspecific = []
    # iterate over every barcode primer pair and match to each oligo to check for nonspecific amplification
    for idxPrime, primers in enumerate(
        grouped
    ):  # iterate over every barcode primer pair
        print("Checking primer set:" + primers[0].id[:-2])
        for idxCassette, fragment in enumerate(
            uCassette
        ):  # iterate over every OLS oligo
            if (
                primers[0].id.split("_")[2] != all_oligos[idxCassette].id.split("_")[2]
            ):  # ignore designed annealing (same name)
                fragname = fragment.id
                fragment = fragment.seq
                non = [[False], [False]]
                for idxDirection, primer in enumerate(primers):
                    primername = primer.id
                    primer = primer.seq
                    for i in range(
                        len(fragment) - len(primer)
                    ):  # iterate over the length of the oligo for a binding site
                        match = [
                            primer[j].lower() == fragment[i + j].lower()
                            for j in range(len(primer))
                        ]
                        first = 10
                        for k in range(len(match) - 3):
                            if (match[k] and match[k + 1] and match[k + 3]) or (
                                match[k] and match[k + 1] and match[k + 2]
                            ):
                                first = k
                                break
                        if (
                            sum(match[first:]) > len(primer[first:]) * 0.8
                            and sum(match[first:]) > 6
                            and match[-1]
                        ):  # string compare - sum of matched nt is greater than 80%
                            try:
                                melt = mt.Tm_NN(
                                    primer[first:],
                                    c_seq=fragment[
                                        i + first : i + len(primer)
                                    ].complement(),
                                    nn_table=mt.DNA_NN2,
                                    de_table=mt.DNA_DE1,
                                    imm_table=mt.DNA_IMM1,
                                )
                                # if melt>20:
                                #     print('Found non-specific match:'+fragment[i:i+len(primer)])
                                #     print('                 primer:'+primer+' Tm:'+str(round(melt,1)))
                                if melt > 35:
                                    non[0].append(True)
                            except ValueError as valerr:
                                # print(str(valerr)+". Please check position manually:"+str(i+1)+" forward")
                                # print('Primer:'+primer)
                                # print('Match: '+fragment[i:i+len(primer)])
                                pass
                    fragment = fragment.reverse_complement()
                    for i in range(len(fragment) - len(primer)):
                        match = [
                            primer[j].lower() == fragment[i + j].lower()
                            for j in range(len(primer))
                        ]
                        first = 10
                        for k in range(0, len(match) - 3, 1):
                            if match[k] and match[k + 1] and match[k + 3]:
                                first = k
                                break
                        if (
                            sum(match[first:]) > len(primer[first:]) * 0.8
                            and sum(match[first:]) > 6
                            and match[-1]
                        ):  # string compare - sum of matched nt is greater than 80%
                            try:
                                melt = mt.Tm_NN(
                                    primer[first:],
                                    c_seq=fragment[
                                        i + first : i + len(primer)
                                    ].complement(),
                                    nn_table=mt.DNA_NN2,
                                    de_table=mt.DNA_DE1,
                                    imm_table=mt.DNA_IMM1,
                                )
                                # if melt > 20:
                                #     print('Found non-specific match:'+fragment[i:i+len(primer)])
                                #     print('                 primer:'+primer+' Tm:'+str(melt))
                                if melt > 35:
                                    non[1].append(True)
                            except ValueError as valerr:
                                # print(str(valerr)+". Please check position manually:"+str(i+1)+" reverse")
                                # print('Primer:'+primer)
                                # print('Match: '+fragment[i:i+len(primer)])
                                pass
                    if sum(non[0]) == 0 and sum(non[1]) == 0:
                        break
                    if sum(non[0]) > 0 and sum(non[1]) > 0:
                        nonspecific.append([primername, fragname])
                        print("Found Non-specific Amplification")
    if nonspecific:
        print("Nonspecific Primers: (Manually changing primer sequence recommended)")
        print(nonspecific)
    else:
        print("No non-specific primers detected")

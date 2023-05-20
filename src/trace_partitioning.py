import sys
import os
import time
import pickle
import pandas as pd
import numpy as np
from tqdm import tqdm
from pathlib import Path
from collections import defaultdict
from datasketch import MinHash, MinHashLSHForest, MinHashLSH
from matplotlib import pyplot as plt
import ipaddress
sys.path.append("../")
from common.evaluation import evaluator
from common.utils import load_pickle, save_pickle


class LocalitySearch():
    def __init__(self, num_perm=50):
        self.num_perm = num_perm
        self.model = None 

    def build_search_db(self, feature_dict, dbtype="threshold", threshold=None):
        db = dict()
        for key, item_set in feature_dict.items():
            db[key] = self.__build_minhash(item_set)

        if dbtype == "topk":
            self.model = MinHashLSHForest(num_perm=self.num_perm)
            for k, v in db.items():
                self.model.add(k, v)
            self.model.index()
        elif dbtype == "threshold":
            assert threshold is not None, "must set threshold when dbtype=threshold."
            self.model = MinHashLSH(threshold=threshold, num_perm=self.num_perm)
            for k, v in db.items():
                self.model.insert(k, v)

    def __build_minhash(self, aset):
        m = MinHash(num_perm=self.num_perm)
        for d in aset:
            m.update(str(d).encode('utf8'))
        return m

    def compute_jaccard(self, a, b):
        a = set(a)
        b = set(b)
        return len(a&b) / len(a|b)

    def query_threshold(self, query):
        query = self.__build_minhash(query)
        results = self.model.query(query)
        return results


# Convert "cluster_id => vmid" to "vmid =? cluster_id"
def get_partitions(vm2partition):
    vm_partitions = defaultdict(list)  # part_id: [vm list]
    for vmid, cluster_id in vm2partition.items():
        vm_partitions[cluster_id].append(vmid)
    num_partitions = len(vm_partitions)
    print(f"Prepartition done, {num_partitions} partitions obtained.")
    return vm_partitions


def is_internal_ip(ip):
    try:
        ip = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return ip.is_private or ip.is_reserved or ip.is_link_local or ip.is_loopback


def get_vm2feats(row, metadata, vm2feats):
    srcip, dstip = row[0], row[1]
    if srcip in metadata:
        vmid = metadata[srcip]["vmid"]
        vm2feats[vmid].add(dstip)

    if dstip in metadata:
        vmid = metadata[dstip]["vmid"]
        vm2feats[vmid].add(srcip)


def trace_partition(vm2feats, threashold):
    build_begin = time.time()
    LS = LocalitySearch()
    LS.build_search_db(vm2feats, dbtype="threshold", threshold=threshold)
    build_end = time.time()
    build_time = build_end - build_begin
    print(f"Build Time: {build_time:.3f}s")

    partition_idx = 0
    vm2partition = dict()
    start = time.time()
    if partition_algorithm == "simple":
        for key, feats in tqdm(vm2feats.items()):
            if key in vm2partition: continue
            neighbor_keys = LS.query_threshold(feats)
            neighbor_keys = [item for item in neighbor_keys if (item != key and item not in vm2partition)]
            if len(neighbor_keys) > 0:
                vm2partition[key] = partition_idx
                for vm in neighbor_keys:
                    vm2partition[vm] = partition_idx
                partition_idx += 1
            else:
                vm2partition[key] = -1
    elif partition_algorithm == "union_set":
            for key, feats in tqdm(vm2feats.items()):
                id1 = vm2id[key]
                neighbor_keys = LS.query_threshold(feats)
                for item in neighbor_keys:
                    id2 = vm2id[item]
                    if Union_Find.parent[id1] == Union_Find.parent[id2]:
                        continue
                    if Union_Find.find(id1) != Union_Find.find(id2):
                        Union_Find.union(id1, id2)
            for vm, idx in vm2id.items():
                root = Union_Find.find(idx)
                if Union_Find.size[root] == 1:
                    vm2partition[vm] = -1
                else:
                    vm2partition[vm] = root
    end = time.time()
    print(f"{end-start:.2f} s")
    return vm2partition


if __name__ == "__main__":
    date = "dataset1"
    trace_path  = Path(f"../data/anonymized_trace.csv")
    metadata_path = Path(f"../data/anonymized_metadata.pkl")
    vm2feats_path = Path(f"../outdir/vm2feats.pkl")
    partition_algorithm = "simple" # or "union_set"
    threshold = 0.1
    if os.path.exists(vm2feats_path):
        vm2feats = load_pickle(Path(vm2feats_path))
        print("Load vm2feats data done.")
    else:
        trace_df = pd.read_csv(trace_path, nrows=None)
        metadata = load_pickle(metadata_path)
        print("Reading data done.")
        
        vm2feats = defaultdict(set)
        tqdm.pandas()
        trace_df.progress_apply(lambda x: get_vm2feats(x, metadata, vm2feats), axis=1, raw=True)
        save_pickle(vm2feats, Path(vm2feats_path))
    print("Total {} VMs.".format(len(vm2feats)))
    
    partition_list_outpath = Path(f"../outdir/threashold_{threshold}.pkl")

    vm2partition = trace_partition(vm2feats, threshold)

    partition_list = get_partitions(vm2partition)
    save_pickle(partition_list, partition_list_outpath)

    function_label_file = f"../data/anonymized_label.csv"
    outdir_root = Path(f"../outdir/partition_list/")
    evaluator(f"threashold={threshold}", outdir_root, function_label_file, "label", True).evaluate_metrics(vm2partition)
import torch
import numpy as np
from models.GAT_model import GAT
from models.GCN_model import GCN
from torch.optim import Adam
from torch.nn.functional import cross_entropy
from dataset import pyg_dataset, pyg_to_dgl
from early_stop import EarlyStopping
from models.BWGNN_model import BWGNN_em
from sklearn.metrics import roc_auc_score
from torch_geometric.nn.conv import GCNConv
from utils import *

torch.manual_seed(21)
device = torch.device("cuda")

def train_for_mul_model(model_list, optimizer_list, linear_model, linear_optimizer, data, a_weight, epochs, b_weight, b_optimizer=None,verbose=1):
    """Train for multi model togeother. Like GAT, GCN, BWGNN or anyelse. 
    Args: 
        b_weight: option for numbers list or learnable parameters
        b_optimizer: None if b_weight is numbers list, real torch optimizer if b_weight belongs to the learnable parameters.
    """
    best_val_auc = 0
    early_stop = EarlyStopping(patience=20)
    for epoch in range(epochs):
        if verbose:
            print (b_weight)
        hiddle_list = []
        for pos, model in enumerate(model_list):
            model.train()
            logits, hid = model(data.x)
            hid = hid * b_weight[0][pos]
            # if pos != 2:
                # hid = hid * 0
            hiddle_list.append(hid)
        # 特征融合以及特征学习
        hiddle = torch.concat(hiddle_list, axis=1)
        hiddle = zero2one((1-cosine_distance(hiddle.T)).mean(axis=0))*hiddle
        
        logits = linear_model(hiddle, data.edge_index)
        train_loss = cross_entropy(logits[data.train_mask], data.y[data.train_mask], weight=torch.tensor([1.0, a_weight],device=device))
        
        for optimizer in optimizer_list:
            optimizer.zero_grad()
        linear_optimizer.zero_grad()
        if b_optimizer != None:
            b_optimizer.zero_grad()
        
        train_loss.backward()
        
        for optimizer in optimizer_list:
            optimizer.step()
        linear_optimizer.step()
        if b_optimizer != None:
            b_optimizer.step()

        hiddle_list = []
        for model in model_list:
            model.eval()
            logits, hid = model(data.x)
            hiddle_list.append(hid)
        # 特征融合以及特征学习
        hiddle = torch.concat(hiddle_list, axis=1)
        logits = linear_model(hiddle, data.edge_index)

        val_loss = cross_entropy(logits[data.val_mask], data.y[data.val_mask], weight=torch.tensor([1.0, a_weight],device=device))
        probs = logits.softmax(1)
        
        auc = roc_auc_score(data.y[data.val_mask].cpu().numpy(), probs[data.val_mask][:,1].detach().cpu().numpy()) if data.y.is_cuda else \
             roc_auc_score(data.y[data.val_mask].numpy(), probs[data.val_mask][:,1].detach().numpy())
        
        if auc >= best_val_auc:
            best_val_auc = auc

        early_stop(val_loss, model)
        if early_stop.early_stop == True:
            print ("Early stopping")
            break
        if verbose:
            print (f"Epoch {epoch+1}/{epochs}: val_loss: {val_loss}, val_auc: {auc}")
    hiddle_list = []
    for model in model_list:
        model.eval()
        logits, hid = model(data.x)
        hiddle_list.append(hid)
    # 特征融合以及特征学习
    hiddle = torch.concat(hiddle_list, axis=1)
    logits = linear_model(hiddle, data.edge_index)
    probs = logits.softmax(1)
    auc = roc_auc_score(data.y[data.test_mask].cpu().numpy(), probs[data.test_mask][:,1].detach().cpu().numpy()) if data.y.is_cuda else \
             roc_auc_score(data.y[data.test_mask].numpy(), probs[data.test_mask][:,1].detach().numpy())
    return hid, auc, best_val_auc

def train_for_param(data_name, dataset_mode="min",verbose=1):
    parameter_list = np.linspace(start=0,stop=1,num=11)
    parameter_list1 = np.linspace(start=0.1,stop=1,num=10)
    param2performance_list = []
    for param1 in parameter_list:
        for param2 in parameter_list1:
            # param2 = 1 - param1
            # run five times to get mean and std for test. best performance for val.
            run_times = 3
            test_auc_mean = []
            test_auc_std = []
            val_auc_best = []
            test_list = []
            val_list = []
            for i in range(run_times):
                np.random.seed(2*i)
                # data = pyg_dataset(dataset_name=data_name, dataset_spilt=[0.4,0.29,0.3]).dataset
                data = pyg_dataset(dataset_name=data_name, dataset_spilt=[0.4,0.29,0.3], anomaly_type=dataset_mode).dataset
                # data2 = pyg_dataset(dataset_name="cora", dataset_spilt=[0.4,0.2,0.3], anomaly_type="min", anomaly_ratio=0.1).dataset
                dgl_data = pyg_to_dgl(data)
                data = data.to(device)
                dgl_data = dgl_data.to(device)

                a_weight = anomaly_weight(data)

                epochs = 75
                hid_dim = 64
                number_class = 2
                gcn_model = GCN(data.x.shape[1], hid_dim, number_class, data).to(device)
                gat_model = GAT(data.x.shape[1], hid_dim, number_class, data).to(device)
                bw_model = BWGNN_em(data.x.shape[1], hid_dim, number_class, dgl_data).to(device)
                model_list = [gcn_model, gat_model, bw_model]
                cla_model = GCNConv(hid_dim*len(model_list), number_class).to(device)

                # l_weight = [nn.Parameter(torch.ones([hid_dom.shape[-1] * feature_length], dtype=torch.float32, requires_grad=True))]
                # b_weight = [nn.Parameter(torch.ones([len(model_list)], dtype=torch.float32, requires_grad=True))]
                b_weight = [[param1, param2, 0.0]]
                # b_optimizer = Adam(b_weight, lr = 1e-2, weight_decay=5e-2)
                # l_optimizer_ = Adam(l_weight, lr = 5e-2, weight_decay=5e-2)

                gcn_optimizer = Adam(gcn_model.parameters(), lr = 1e-3)
                gat_optimizer = Adam(gat_model.parameters(), lr = 1e-3)
                bw_optimizer = Adam(bw_model.parameters(), lr = 1e-3)
                cla_optimizer = Adam(cla_model.parameters(), lr = 1e-3)

                optimizer_list = [gcn_optimizer, gat_optimizer, bw_optimizer]
                _, auc, best_val_auc = train_for_mul_model(model_list, optimizer_list, cla_model, cla_optimizer, data, a_weight, epochs, b_weight,verbose=verbose)
                
                test_list.append(auc)
                val_list.append(best_val_auc)
            
            test_auc_mean.append(np.array(test_list).mean())
            test_auc_std.append(np.array(test_list).std())
            val_auc_best.append(np.array(val_list).max())

            param2performance_list.append([param1, param2, test_auc_mean[-1], test_auc_std[-1], val_auc_best[-1]])
            print (test_list,f"\n Test mean: {np.array(test_list).mean()}",f"\n Test std: {np.array(test_list).std()}")
            print (val_list, f"\n Val best: {np.array(val_list).max()}")
        
    np.savetxt(f"./result/param2performance_{data_name}_{dataset_mode}_gcngat.txt", np.array(param2performance_list))

# "fraud_amazon",
dataset_ava_list = ["amazon_photo","weibo"]
# dataset_ava_list = ["weibo"] #7*2 = 14
# dataset_ava_list = ["weibo"]
dataset_mode_list = ["min", "syn"]
for dataset_mode in dataset_mode_list:
    for data_name in dataset_ava_list:
        train_for_param(data_name, dataset_mode, verbose=0)
    
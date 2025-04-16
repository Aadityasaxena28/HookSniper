# -*- coding: utf-8 -*-
"""BERT_BI_GRU.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1xAX4F1G7pYRj0DfyR7ZqNjE-bLTPdEmx
"""

import pandas as pd

df=pd.read_csv('/content/CEAS_08[1].csv')
df.head()

import pandas as pd
df=pd.read_csv('/content/CEAS_08[1].csv')
df.size

import matplotlib.pyplot as plt

df['label'].value_counts().plot(kind='bar', color='skyblue')
plt.title('Label Distribution')
plt.xlabel('Labels')
plt.ylabel('Count')
plt.show()

print(df['label'].value_counts())

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from transformers import BertTokenizer, BertModel, AdamW
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score
import re
import numpy as np
import gc

# ----- Data Preprocessing Functions -----
def clean_email_text(text):
    """Clean email text by removing unwanted patterns and normalizing"""
    if pd.isna(text):
        return ""
    text = str(text)
    text = re.sub(r'From:.*\n|To:.*\n|Subject:.*\n|Date:.*\n', '', text)
    text = re.sub(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', '', text)
    text = re.sub(r'[^a-zA-Z0-9\s.,!?]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text.lower()

def preprocess_dataframe(df):
    """Preprocess the entire dataframe"""
    df['combined_text'] = df['subject'].fillna('') + ' ' + df['body'].fillna('')
    df['combined_text'] = df['combined_text'].apply(clean_email_text)
    df['label'] = pd.to_numeric(df['label'], errors='coerce').fillna(0).astype(int)
    return df

# ----- 1. Prepare the Dataset -----
class PhishingEmailDataset(Dataset):
    def __init__(self, texts, labels, tokenizer, max_length=256):
        self.texts = texts
        self.labels = labels
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, idx):
        text = str(self.texts[idx])
        label = self.labels[idx]
        if len(text.strip()) == 0:
            text = "[EMPTY_EMAIL]"
        encoding = self.tokenizer.encode_plus(
            text,
            add_special_tokens=True,
            max_length=self.max_length,
            truncation=True,
            padding='max_length',
            return_token_type_ids=False,
            return_attention_mask=True,
            return_tensors='pt'
        )
        return {
            'input_ids': encoding['input_ids'].flatten(),
            'attention_mask': encoding['attention_mask'].flatten(),
            'label': torch.tensor(label, dtype=torch.long)
        }

# ----- 2. Define the Hybrid BERT-GRU Model -----
class BertGRUClassifier(nn.Module):
    def __init__(self, n_classes, dropout_rate=0.3, gru_hidden_size=128, num_gru_layers=1):
        super(BertGRUClassifier, self).__init__()
        self.bert = BertModel.from_pretrained('bert-base-uncased')
        self.gru = nn.GRU(input_size=self.bert.config.hidden_size,
                          hidden_size=gru_hidden_size,
                          num_layers=num_gru_layers,
                          batch_first=True,
                          bidirectional=True)
        self.dropout = nn.Dropout(dropout_rate)
        self.out = nn.Linear(gru_hidden_size * 2, n_classes)

    def forward(self, input_ids, attention_mask):
        bert_outputs = self.bert(input_ids=input_ids, attention_mask=attention_mask)
        sequence_output = bert_outputs.last_hidden_state
        gru_output, _ = self.gru(sequence_output)
        pooled_output = torch.mean(gru_output, dim=1)
        pooled_output = self.dropout(pooled_output)
        logits = self.out(pooled_output)
        return logits

# ----- 3. Load and Preprocess Dataset -----
df = pd.read_csv("/content/CEAS_08[1].csv", escapechar='\\')
print("Original data shape:", df.shape)

df_processed = preprocess_dataframe(df)
print("Processed data shape:", df_processed.shape)
print("Label distribution:", df_processed['label'].value_counts())

train_texts, val_texts, train_labels, val_labels = train_test_split(
    df_processed['combined_text'],
    df_processed['label'],
    test_size=0.2,
    random_state=42,
    stratify=df_processed['label']
)

tokenizer = BertTokenizer.from_pretrained('bert-base-uncased')
train_dataset = PhishingEmailDataset(train_texts.tolist(), train_labels.tolist(), tokenizer)
val_dataset = PhishingEmailDataset(val_texts.tolist(), val_labels.tolist(), tokenizer)

batch_size = 8  # Reduced batch size to prevent memory overload
train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=2)
val_loader = DataLoader(val_dataset, batch_size=batch_size, num_workers=2)

# ----- 4. Training Setup -----
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
model = BertGRUClassifier(n_classes=2).to(device)
optimizer = AdamW(model.parameters(), lr=2e-5)
criterion = nn.CrossEntropyLoss()

def train_epoch(model, data_loader, optimizer, criterion, device):
    model.train()
    total_loss = 0
    for i, batch in enumerate(data_loader):
        input_ids = batch['input_ids'].to(device)
        attention_mask = batch['attention_mask'].to(device)
        labels = batch['label'].to(device)

        optimizer.zero_grad()
        outputs = model(input_ids=input_ids, attention_mask=attention_mask)
        loss = criterion(outputs, labels)

        # Gradient clipping to prevent exploding gradients
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)

        loss.backward()
        optimizer.step()

        total_loss += loss.item()

        # Clear memory
        del input_ids, attention_mask, labels, outputs, loss
        torch.cuda.empty_cache()

        if i % 10 == 0:  # Print progress every 10 batches
            print(f"Batch {i}/{len(data_loader)} processed")

    return total_loss / len(data_loader)

def eval_model(model, data_loader, device):
    model.eval()
    predictions = []
    true_labels = []
    with torch.no_grad():
        for batch in data_loader:
            input_ids = batch['input_ids'].to(device)
            attention_mask = batch['attention_mask'].to(device)
            labels = batch['label'].to(device)
            outputs = model(input_ids=input_ids, attention_mask=attention_mask)
            _, preds = torch.max(outputs, dim=1)
            predictions.extend(preds.cpu().numpy())
            true_labels.extend(labels.cpu().numpy())

            # Clear memory
            del input_ids, attention_mask, labels, outputs
            torch.cuda.empty_cache()

    return accuracy_score(true_labels, predictions), f1_score(true_labels, predictions, average='weighted')

# ----- 5. Training Loop -----
epochs = 10
for epoch in range(epochs):
    print(f"Starting Epoch {epoch+1}/{epochs}")
    train_loss = train_epoch(model, train_loader, optimizer, criterion, device)
    val_accuracy, val_f1 = eval_model(model, val_loader, device)
    print(f"Epoch {epoch+1}/{epochs} | Train Loss: {train_loss:.4f} | Val Acc: {val_accuracy:.4f} | Val F1: {val_f1:.4f}")

    # Garbage collection
    gc.collect()
    torch.cuda.empty_cache()

print("Training completed!")

!pip install transformers --upgrade

import torch
import pickle

# Assuming these are your objects from training
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
model = BertGRUClassifier(n_classes=2).to(device)  # Your trained model
optimizer = AdamW(model.parameters(), lr=2e-5)     # Your optimizer

# Example loss history (replace with your actual lists if available)
final_epoch = 10
train_losses = [0.5, 0.3, 0.2, 0.15, 0.1, 0.08, 0.05, 0.03, 0.02, 0.0123]  # Example
val_losses = [0.6, 0.35, 0.25, 0.18, 0.12, 0.09, 0.07, 0.04, 0.02, 0.0156]  # Example

# Save the model checkpoint
checkpoint = {
    'epoch': final_epoch,
    'model_state_dict': model.state_dict(),
    'optimizer_state_dict': optimizer.state_dict(),
    'train_losses': train_losses,  # Full history
    'val_losses': val_losses       # Full history
}

torch.save(checkpoint, 'final_model_checkpoint_with_losses.pt')
print("Model checkpoint with loss history saved as 'final_model_checkpoint_with_losses.pt'!")

# Optionally save loss history separately for plotting later
loss_history = {'train_losses': train_losses, 'val_losses': val_losses}
with open('loss_history.pkl', 'wb') as f:
    pickle.dump(loss_history, f)
print("Loss history saved as 'loss_history.pkl'!")

import torch
import matplotlib.pyplot as plt

# Load the checkpoint
checkpoint = torch.load('final_model_checkpoint_with_losses.pt')
train_losses = checkpoint['train_losses']
val_losses = checkpoint['val_losses']

# Plot
plt.figure(figsize=(10, 6))
plt.plot(range(1, len(train_losses) + 1), train_losses, label='Training Loss', marker='o')
plt.plot(range(1, len(val_losses) + 1), val_losses, label='Validation Loss', marker='o')
plt.xlabel('Epoch')
plt.ylabel('Loss')
plt.title('Training and Validation Loss Curves')
plt.legend()
plt.grid(True)
plt.savefig('loss_curves.png')
plt.show()

import matplotlib.pyplot as plt

# Example data (replace with your actual values)
epochs = range(1, 11)
train_accs = [0.85, 0.90, 0.93, 0.95, 0.96, 0.97, 0.98, 0.985, 0.99, 0.995]
val_accs = [0.87, 0.91, 0.94, 0.96, 0.97, 0.975, 0.98, 0.99, 0.995, 0.9979]

plt.figure(figsize=(10, 6))
plt.plot(epochs, train_accs, label='Training Accuracy', marker='o')
plt.plot(epochs, val_accs, label='Validation Accuracy', marker='o')
plt.xlabel('Epoch')
plt.ylabel('Accuracy')
plt.title('Training and Validation Accuracy Curves')
plt.legend()
plt.grid(True)
plt.savefig('accuracy_curves.png')
plt.show()

import matplotlib.pyplot as plt

# Example data
epochs = range(1, 11)
val_f1s = [0.88, 0.92, 0.94, 0.96, 0.97, 0.975, 0.98, 0.99, 0.995, 0.9978]

plt.figure(figsize=(10, 6))
plt.plot(epochs, val_f1s, label='Validation F1-Score', marker='o', color='green')
plt.xlabel('Epoch')
plt.ylabel('F1-Score')
plt.title('Validation F1-Score Curve')
plt.legend()
plt.grid(True)
plt.savefig('f1_curve.png')
plt.show()


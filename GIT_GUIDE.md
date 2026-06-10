# 🚀 Guide: How to Push this Project to GitHub

This guide walks you through initializing a Git repository, staging files safely, and pushing the code to a new GitHub repository.

---

## ⚠️ Important Checklist before Pushing
Make sure all private sessions, local databases, and private credentials are scrubbed (already done in this workspace):
- [x] `.env` files contain no sensitive keys (only blank placeholders).
- [x] MTProto `.session` and `.session-journal` files are deleted.
- [x] SQLite `.db`, `.db-wal`, and `.db-shm` files are deleted.
- [x] `.gitignore` contains patterns to ignore these files in the future.

---

## Step-by-Step Pushing Guide

### Step 1: Open your Terminal / command prompt
Navigate to your project directory:
```bash
cd a:\telegr
```

### Step 2: Initialize Git
If you haven't initialized Git in this folder yet, run:
```bash
git init
```

### Step 3: Verify the Gitignore
Ensure Git ignores the correct files by checking the status. Database and session files should **not** show up under untracked files:
```bash
git status
```

### Step 4: Stage the files
Add all the files to the commit staging area:
```bash
git add .
```

### Step 5: Create your first commit
Commit the files with a meaningful message:
```bash
git commit -m "Initial commit: Movie Indexer Bot & Channel Migrator"
```

### Step 6: Create a new repository on GitHub
1. Go to [GitHub](https://github.com) and log in.
2. Click the **New** button to create a new repository.
3. Choose a name (e.g. `telegram-movie-indexer`).
4. Keep **Initialize this repository with:** unchecked (do not add a README, license, or .gitignore from the GitHub UI, as we already have them).
5. Click **Create repository**.

### Step 7: Link your local repository to GitHub
Copy the URL of your new GitHub repository, then run (substituting your actual GitHub repository URL):
```bash
# Set main branch
git branch -M main

# Add remote origin
git remote add origin https://github.com/your-username/telegram-movie-indexer.git
```

### Step 8: Push the code to GitHub
Push your local main branch to the remote origin:
```bash
git push -u origin main
```

---

## 🔄 How to Pull Updates later
If you edit or pull files on another machine, you can sync back by running:
```bash
git pull origin main
```

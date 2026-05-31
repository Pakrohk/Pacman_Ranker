# PacmanRanker

**PacmanRanker** is a fast, asynchronous Command Line Interface (CLI) tool designed to benchmark and rank **Arch Linux** and **Chaotic-AUR** mirrors. Built with Python, `asyncio`, and `aiohttp`, it ensures rapid concurrent testing, while `rich` provides a beautiful and informative terminal user interface.

## 🚀 Features

- **Asynchronous Benchmarking:** Blazing fast concurrent mirror testing using `asyncio` and `aiohttp`.
- **Dual Repository Support:** Benchmark both standard Arch Linux mirrors and Chaotic-AUR mirrors.
- **Smart Scoring Algorithm:** Ranks mirrors based on a balanced metric of download speed, latency, and reliability (failure rate).
- **Beautiful Terminal UI:** Progress bars and formatted tables powered by `rich`.
- **Modern Project Management:** Managed and built entirely with [Rye](https://rye-up.com/).

## 🧮 Scoring Model

Mirrors are ranked using a custom formula designed to prioritize fast, stable, and responsive servers:

$$ Score = \frac{Speed}{1.0 + Latency + Failures} $$

- **Speed:** The average download speed from the mirror.
- **Latency:** The time taken to establish a connection.
- **Failures:** Penalizes mirrors that timeout or return errors.

## 📋 Prerequisites

- Python 3.8 or higher.
- [Rye](https://rye-up.com/) (The Python packaging and dependency management tool).

## 🛠️ Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Pakrohk/Pacman_Ranker.git
   cd pacmanranker
   ```

2. **Sync the project with Rye:**
   This command will automatically create the virtual environment and install all required dependencies (like `aiohttp` and `rich`).
   ```bash
   rye sync
   ```

## 💻 Usage

PacmanRanker can be run easily via Rye. 

**Benchmark standard Arch Linux mirrors:**
```bash
rye run pacmanranker
```
**Benchmark Chaotic-AUR mirrors:**
Use the `--chaotic` flag to switch the target mirror list.
```bash
rye run pacmanranker --chaotic
```

## 🤝 Contributing
Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

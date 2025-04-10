<div align="center">
    <img src="src/reacher/assets/reacher-icon-banner.png" alt="REACHER logo">
</div>
<br>
<br>

[![Download REACHER](https://img.shields.io/badge/Download-REACHER-cyan?style=for-the-badge)](#download-options)

*Written by*: Joshua Boquiren ([*@thejoshbq*](https://github.com/thejoshbq/thejoshbq/))

## Overview

The **REACHER** (Rodent Experiment Application Controls and Handling Ecosystem for Research) Suite is an open-source application framework designed for experimental paradigms involving head-fixed rodents. It supports connections to multiple microcontrollers and facilitates running multiple sessions simultaneously from the same computer or across distributed systems.

---

#### **Key Features:**
1. **Serial Data Handling**:
   - Two-threaded system for serial communication:
     - One thread reads data from the microcontroller and queues it.
     - Another processes the queued data, ensuring no data loss.
   - String-based communication for easy debugging and logging.

2. **Thread Flags for Control**:
   - Flags ensure smooth data collection, pausing, and resuming:
     - `serial_flag`: Controls serial thread activity.
     - `program_flag`: Manages program execution states (e.g., paused or running).

3. **Data Logging and Integrity**:
   - Behavioral and frame data are logged to CSV files and processed into Pandas DataFrames.
   - If no destination or filename is specified, data is saved to a default directory (`~/REACHER/`).

---

## **Technical Highlights**

### **Data Visualization and Monitoring**
- Real-time event tracking using `Plotly` for clear and interactive visualizations.
- Tabular summaries of behavioral data for detailed analysis.

<br>

## **Recommended Specifications**

| **Component** | **Minimum Specs** | **Recommended Specs** | **High-Performance Specs** |
|------------------------|------------------------------------------|------------------------------------------|-----------------------------------------|
| **CPU**               | Quad-core processor (e.g., Intel i3)     | 6-core or 8-core processor (e.g., Intel i5/i7, AMD Ryzen 5) | 12-core or higher (e.g., AMD Ryzen 9, Intel i9) |
| **RAM**               | 8 GB                                     | 16 GB                                    | 32 GB or higher                         |
| **Storage**           | 256 GB SSD                               | 512 GB SSD                               | 1 TB NVMe SSD or higher                 |
| **Operating System**  | Linux or Windows (64-bit)                | Linux (Ubuntu/Debian preferred), Windows (64-bit), or macOS | Linux (optimized with custom kernels)   |
| **Cooling**           | Basic air cooling                       | Efficient air cooling or entry-level liquid cooling | High-end liquid cooling                 |
| **GPU (Optional)**    | Integrated graphics                     | Mid-range GPU (e.g., NVIDIA GTX 1660)   | High-end GPU (e.g., NVIDIA RTX 3080)    |

---

<br><br>
<div align="center">
  <h2>Copyright & License</h2>
  <p>© 2025 Otis Lab. All rights reserved.</p>
  <p>This project is licensed under the <a href=""><strong>LICENSE</strong></a>.</p>
  <p>For more information, please visit the official <a href=""><strong>documentation repository</strong></a>, or contact the author at <a href="mailto:thejoshbq@proton.me"><i>thejoshbq@proton.me</i></a>
</div>

<div align="center", style="padding: 10px; background-color: #333; color: white;">
    <p><i>"The heavens declare the glory of God, and the sky above proclaims his handiwork."</i>
    <p>Psalm 19:1</p>
</div>

# Ultima Thule Encoder
UTE is a project for doing distributed video transcoding from H264 to H265. UTE focuses on completing each task as quickly as possible (as opposed to multiple tasks in parallel) by breaking it up into smaller units of work. It uses Docker Swarm as the basis for creating a simple, easy to install foundation for connecting multiple heterogeneous systems together. UTE then runs Python Celery on top of this layer to provide a way for distributed tasking across the swarm. UTE currently only runs on Linux but will soon include Windows (x86/64 only).


### How it works
- Once installed and configured, UTE waits for work in the form of H264 MKV files placed in an NFS shared folder
- Each file is in turn analyzed via 'ffprobe' and 'mediainfo' tools to get file information
- Using this info, UTE creates a number of tasks, each of which has a chunk of frame numbers assigned to it
- These tasks are distributed to the swarm asynchronously via a broker (Redis)
- Each swarm worker receives a task, pulls its chunk of frames from the network shared file, and reencodes those frames using x265 (and whatever settings the user chooses)
- Completed tasks are placed as raw data files into a second NFS shared folder, labeled and numbered accordinly
- When every task from that job is complete, another function recombines all the chunks into an H265 Matroska file
- Rinse and repeat

### Instructions (work in progress)
1) Download and install DockerCE on each machine
2) Ensure docker daemon is running and containers work (ie. Docker Hello World test)
3) Download this project to the machine you want to use for managing the swarm (can be a desktop or a headless system, but ideally is local to your network to access the web UI)
4) Build docker service images (instructions coming soon...)
5) Configure 'config/docker-stack.yml' and 'config/hosts.ini'
6) Run 'UTEncoder.sh up'
7) Put video file(s) in your configured NFS share


# Ultima Thule Encoder
UTE is a project for doing distributed video transcoding to HEVC (x265). Unlike some distributed encoding tools, UTE focuses on completing each task as quickly as possible (as opposed to multiple tasks in parallel), by breaking the video up into smaller units of work. It uses Docker Swarm as the basis for creating a simple, easy to install foundation for connecting multiple heterogeneous systems together. On top of the swarm is Python, Redis, and RQ. UTE currently only runs on Linux but will soon include Windows.

This project is still in development.


### Notable Features
- Uses FFMPEG and x265 for broad format detection and excellent quality/compression
- Distributed computing using Docker Swarm
- Uses CPU resources efficiently (and fully), by changing the number of threads per encoder instance, and allowing multiple encoder instances on one physical machine (ex. x265 works best with 6-8 threads, so a 32 thread machine could use 4 workers of 8 threads each)
- No time wasted. As one movie is wrapping up, the next video starts encoding on free workers.
- No long pre-processing times. A full length movie is typically pre-processed in about 10-15 seconds
- Works on Windows 10 and Linux
- Low bandwidth requirements. Workers only need to access the section of video assigned to them. No video seeking over the network
- Flexibility to use any settings available in FFMPEG and x265 encoder
- Coming Soon: Vapoursynth integration as a pre-processor for effects, grain-removal, etc


### How it works
- Once installed and configured, UTE polls an NFS share (inbox) looking for video files
- Each file is in turn analyzed via 'ffprobe' and 'mediainfo' tools to get file information
- Using this info, UTE creates a number of tasks, each of which has a chunk of frame numbers assigned to it
- These tasks are distributed to the swarm asynchronously via a broker (Redis database) using the RQ (Redis Queue) framework
- Each swarm worker receives a task, pulls its chunk of frames from the network shared file, and reencodes those frames using x265 (and whatever settings the user chooses)
- Completed tasks are placed as raw data files into a second NFS shared folder (outbox), labeled and numbered accordinly
- When every task from that job is complete, UTE recombines all the chunks into an HEVC Matroska file, performs error checking and correction, and deletes or moves (depending on user settings) the original file
- Rinse and repeat


### Instructions (work in progress)
1) Download and install DockerCE on each machine you want UTE to use
2) Ensure docker daemon is running and containers work (ie. Docker Hello World test)
3) Download this project to the machine you want to use for managing the swarm (can be a desktop or a headless system, but ideally is local to your network to have best bandwidth performance for rebuilding final file)
4) Build UTE images:
  - browse to the project's top folder (likely 'ultima-thule-encoder')
  - run "bin/build-img.sh all"
  - wait
5) Configure 'config/docker-stack.yml'
  - add worker hosts and a manager
  - add NFS 'inbox' and 'outbox' shared folders
6) Run 'UTEncoder.sh up'
7) Put video file(s) in your configured NFS share
8) Run "bin/follow.sh" to see realtime output of progress


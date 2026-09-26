A digicam folder has JPG and AVI files that need to be put into an archive.

The digicam has the photos under DCIM/104_FUJI DCIM/103_FUJI etc

The archive is a directory with a subdirectory for each month 26-08 means August 2026

Within a month's directory we have directories per event/outing. Like "26-08-23 Uni Friends Hangout" that has images and videos for that outing.

We need to create a command line program that reads the digicam folder and partitions photos into seperate events based on the timestamps, following the thought that the photos from the same event are taken close together.

The program then shows the user the partition, maybe as a list, which allows them to put or remove partitions in the list to adjust them.
There should be a photo preview on the right and the list on the left. The preview does not have to be pixel perfect, it can be the best possible image rendering using ascii that can be done on the command line, same for the videos.

Once the partitions are done, each partition is given a name one by one by the user. And once that is done, the data is copied to the archive.

The jpeg images get copy as is, but the .AVI files must be converted to mp4. I currently do it using handbrake, so try and use the same underlying commands for the conversion, and show an interactive terminal ui that shows the progress and the count of videos left.

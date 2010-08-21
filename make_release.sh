#!/bin/bash

if [ $# -ne 1 ]; then
    echo Usage: $0 version
    exit
fi

v=$1
dir=fortwrap-$1

cd tests
for t in `ls -d */`; do
    echo $t
done


# Clean up test directories
cd tests
for t in `ls -d */`; do
    cd $t
    make realclean
    cd ..
done
cd ..

mkdir $dir
cp -r docs fortwrap.py LICENSE.txt README.txt ReleaseNotes.txt run_tests.py tests $dir
tar --exclude .svn -cvzf $dir.tar.gz $dir

echo "Make sure to update Version number in python source"

# Create version-specific release notes file for upload
cp ReleaseNotes.txt release-notes-$v.txt
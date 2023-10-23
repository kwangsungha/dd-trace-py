#!/bin/bash
ddprof_ver="0.15.0"
ddprof_tag=$ddprof_ver-rc # only because this was an RC release and named as such
ddprof_file="ddprof-$ddprof_ver-amd64-linux.tar.xz"
ddprof_url=https://github.com/DataDog/ddprof/releases/download/v$ddprof_tag/$ddprof_file
curl -LO $ddprof_url
tar -xvf $ddprof_file 

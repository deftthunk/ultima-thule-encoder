#!/bin/bash

mkdir -p ../../staging
var=`docker swarm join-token worker | grep docker`

echo '#!/bin/bash' > ../../staging/token_cmd.sh
echo $var >> ../../staging/token_cmd.sh
chmod +x ../../staging/token_cmd.sh

#!/bin/bash

mkdir -p ../../worker_staging
var=`docker swarm join-token worker | grep docker`

echo '#!/bin/bash' > ../../worker_staging/token_cmd.sh
echo $var >> ../../worker_staging/token_cmd.sh
chmod +x ../../worker_staging/token_cmd.sh

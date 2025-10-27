kubectl logs -n istio-system -l app=jaeger --tail=10
{"level":"info","ts":1761590261.5799627,"caller":"transport/http2_server.go:299","msg":"[transport] [server-transport 0x400020e4e0] Closing: connection error: desc = \"transport: http2Server.HandleStreams received bogus greeting from client: \\\"GET / HTTP/1.1\\\\r\\\\nHost: lo\\\"\""}
{"level":"info","ts":1761590261.5800188,"caller":"grpc@v1.72.2/server.go:1000","msg":"[core] [Server #6]grpc: Server.Serve failed to create ServerTransport: connection error: desc = \"transport: http2Server.HandleStreams received bogus greeting from client: \\\"GET / HTTP/1.1\\\\r\\\\nHost: lo\\\"\""}
{"level":"info","ts":1761590337.060711,"caller":"transport/http2_server.go:299","msg":"[transport] [server-transport 0x400041aea0] Closing: connection error: desc = \"transport: http2Server.HandleStreams received bogus greeting from client: \\\"GET / HTTP/1.1\\\\r\\\\nHost: lo\\\"\""}
{"level":"info","ts":1761590337.0611742,"caller":"grpc@v1.72.2/server.go:1000","msg":"[core] [Server #6]grpc: Server.Serve failed to create ServerTransport: connection error: desc = \"transport: http2Server.HandleStreams received bogus greeting from client: \\\"GET / HTTP/1.1\\\\r\\\\nHost: lo\\\"\""}
{"level":"info","ts":1761590337.0638893,"caller":"transport/http2_server.go:299","msg":"[transport] [server-transport 0x400041b040] Closing: connection error: desc = \"transport: http2Server.HandleStreams received bogus greeting from client: \\\"GET / HTTP/1.1\\\\r\\\\nHost: lo\\\"\""}
{"level":"info","ts":1761590337.0639906,"caller":"grpc@v1.72.2/server.go:1000","msg":"[core] [Server #6]grpc: Server.Serve failed to create ServerTransport: connection error: desc = \"transport: http2Server.HandleStreams received bogus greeting from client: \\\"GET / HTTP/1.1\\\\r\\\\nHost: lo\\\"\""}
{"level":"info","ts":1761590337.0678546,"caller":"transport/http2_server.go:299","msg":"[transport] [server-transport 0x400020eb60] Closing: connection error: desc = \"transport: http2Server.HandleStreams received bogus greeting from client: \\\"GET / HTTP/1.1\\\\r\\\\nHost: lo\\\"\""}
{"level":"info","ts":1761590337.0679233,"caller":"grpc@v1.72.2/server.go:1000","msg":"[core] [Server #6]grpc: Server.Serve failed to create ServerTransport: connection error: desc = \"transport: http2Server.HandleStreams received bogus greeting from client: \\\"GET / HTTP/1.1\\\\r\\\\nHost: lo\\\"\""}
{"level":"info","ts":1761590337.0713756,"caller":"transport/http2_server.go:299","msg":"[transport] [server-transport 0x400020ed00] Closing: connection error: desc = \"transport: http2Server.HandleStreams received bogus greeting from client: \\\"GET / HTTP/1.1\\\\r\\\\nHost: lo\\\"\""}
{"level":"info","ts":1761590337.071414,"caller":"grpc@v1.72.2/server.go:1000","msg":"[core] [Server #6]grpc: Server.Serve failed to create ServerTransport: connection error: desc = \"transport: http2Server.HandleStreams received bogus greeting from client: \\\"GET / HTTP/1.1\\\\r\\\\nHost: lo\\\"\""}
daniel.florezramirez@RVCX661K4W ~ % kubectl describe svc tracing -n istio-system
Name:                     tracing
Namespace:                istio-system
Labels:                   app=jaeger
Annotations:              <none>
Selector:                 app=jaeger
Type:                     ClusterIP
IP Family Policy:         SingleStack
IP Families:              IPv4
IP:                       10.106.0.146
IPs:                      10.106.0.146
Port:                     http-query  80/TCP
TargetPort:               16686/TCP
Endpoints:                10.244.0.11:16686
Port:                     grpc-query  16685/TCP
TargetPort:               16685/TCP
Endpoints:                10.244.0.11:16685
Session Affinity:         None
Internal Traffic Policy:  Cluster
Events:                   <none>

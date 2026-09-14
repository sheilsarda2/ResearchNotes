import subprocess,socket,time,json,os,urllib.request
p=subprocess.Popen(['/release/zenohd','--rest-http-port','18000','--plugin-search-dir','/release','--no-multicast-scouting'],stdout=open('/tmp/zenoh-sse.log','w'),stderr=subprocess.STDOUT)
def counts():
 states={}
 for file in ['/proc/net/tcp','/proc/net/tcp6']:
  for line in open(file).readlines()[1:]:
   f=line.split()
   if int(f[1].split(':')[1],16)==18000:states[f[3]]=states.get(f[3],0)+1
 return {'states':states,'fds':len(os.listdir(f'/proc/{p.pid}/fd'))}
try:
 for i in range(100):
  try:
   s=socket.create_connection(('127.0.0.1',18000),.1);s.close();break
  except OSError:time.sleep(.1)
 else:raise RuntimeError(open('/tmp/zenoh-sse.log').read())
 time.sleep(.3);out={'baseline':counts()}
 for _ in range(20):
  s=socket.create_connection(('127.0.0.1',18000));s.settimeout(2)
  s.sendall(b'GET /demo/** HTTP/1.1\r\nHost: localhost\r\nAccept: text/event-stream\r\n\r\n')
  b=s.recv(4096);assert b'200 OK' in b,b;s.shutdown(socket.SHUT_RDWR);s.close()
 time.sleep(1);out['after_idle_disconnect']=counts()
 # Positive control: leave one stream live, publish, and observe delivery.
 s=socket.create_connection(('127.0.0.1',18000));s.settimeout(2);s.sendall(b'GET /demo/** HTTP/1.1\r\nHost: localhost\r\nAccept: text/event-stream\r\n\r\n');s.recv(4096)
 urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18000/demo/x',data=b'probe',method='PUT')).read()
 b=s.recv(4096);out['live_event_received']=b'cHJvYmU=' in b;out['live_event_raw']=b.decode();s.close()
 time.sleep(1);out['after_publish']=counts();print(json.dumps(out,indent=2))
finally:
 p.terminate();p.wait(timeout=15)

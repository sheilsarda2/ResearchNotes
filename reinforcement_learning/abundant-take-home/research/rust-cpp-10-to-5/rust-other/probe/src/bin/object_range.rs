use object_store::{ClientOptions, GetOptions, GetRange, ObjectStore, http::HttpBuilder, path::Path};
use std::{io::{Read, Write}, net::TcpListener};
use serde_json::json;
#[tokio::main(flavor = "current_thread")]
async fn main() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let server = std::thread::spawn(move || {
        for _ in 0..5 {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request=Vec::new();
            loop { let mut buf=[0;1024]; let n=stream.read(&mut buf).unwrap(); if n==0 {break;} request.extend_from_slice(&buf[..n]); if request.windows(4).any(|s|s==b"\r\n\r\n") {break;} }
            let request=String::from_utf8(request).unwrap();
            let path=request.split_whitespace().nth(1).unwrap();
            let (status,body,extra)=match path {
                "/partial206" => ("206 Partial Content", "hello world", "Content-Range: bytes 0-10/12\r\n"),
                "/full206" => ("206 Partial Content", "hello world!", "Content-Range: bytes 0-11/12\r\n"),
                "/full200" => ("200 OK", "hello world!", "Content-Range: bytes 0-11/12\r\n"),
                _ => ("200 OK", "hello world!", ""),
            };
            let response=format!("HTTP/1.1 {status}\r\nContent-Length: {}\r\n{extra}Connection: close\r\n\r\n{body}",body.len());
            stream.write_all(response.as_bytes()).unwrap();
        }
    });
    let store=HttpBuilder::new().with_url(format!("http://127.0.0.1:{port}"))
        .with_client_options(ClientOptions::new().with_allow_http(true)).build().unwrap();
    let mut outputs=vec![];
    for (path,range) in [("plain",None),("partial206",Some(0..11)),("full206",Some(0..12)),("full200",Some(0..12)),("ignored_partial200",Some(0..11))] {
        let result=store.get_opts(&Path::from(path), GetOptions { range:range.map(GetRange::Bounded), ..Default::default() }).await;
        outputs.push(match result { Ok(r)=>json!({"case":path,"ok":true,"range":format!("{:?}",r.range),"body":String::from_utf8(r.bytes().await.unwrap().to_vec()).unwrap()}),Err(e)=>json!({"case":path,"ok":false,"error":e.to_string()}) });
    }
    server.join().unwrap();
    println!("{}",json!({"cases":outputs}));
    assert!(outputs[0]["ok"].as_bool().unwrap());
    assert!(outputs[1]["ok"].as_bool().unwrap());
    assert!(outputs[2]["ok"].as_bool().unwrap());
    assert!(!outputs[4]["ok"].as_bool().unwrap(),"ignored true-subrange must remain an error");
    assert!(outputs[3]["ok"].as_bool().unwrap(),"200 full-object range response is sufficient");
}

//! Independent HTTP behavior tests; no implementation internals are used.
use std::{io::{Read, Write}, net::TcpListener, sync::mpsc, thread, time::Duration};
use futures_util::StreamExt;
use object_store::{Attribute, ClientOptions, GetOptions, GetRange, ObjectStore, ObjectStoreExt, RetryConfig, http::{HttpBuilder, HttpStore}, path::Path};

struct Reply { range: Option<String>, response: Vec<u8>, release: Option<mpsc::Receiver<()>> }
struct Server { store: HttpStore, thread: thread::JoinHandle<()> }
impl Server {
    fn new(replies: Vec<Reply>) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let thread = thread::spawn(move || {
            for reply in replies {
                let (mut conn, _) = listener.accept().unwrap();
                conn.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
                let mut input = Vec::new();
                while !input.windows(4).any(|x| x == b"\r\n\r\n") {
                    let mut buf = [0;1024]; let n = conn.read(&mut buf).unwrap();
                    assert!(n>0, "request ended before headers"); input.extend_from_slice(&buf[..n]);
                }
                let input = String::from_utf8(input).unwrap();
                let range = input.lines().find_map(|line| line.split_once(':').filter(|(k,_)| k.eq_ignore_ascii_case("range")).map(|(_,v)|v.trim().to_string()));
                assert_eq!(range, reply.range, "actual HTTP request: {input}");
                conn.write_all(&reply.response).unwrap(); conn.flush().unwrap();
                // Retry tests explicitly acknowledge receipt of the first data
                // frame before the server closes an incomplete response.
                if let Some(release) = reply.release { release.recv_timeout(Duration::from_secs(5)).unwrap(); }
            }
        });
        let store = HttpBuilder::new().with_url(format!("http://{addr}"))
            .with_client_options(ClientOptions::new().with_allow_http(true).with_timeout(Duration::from_secs(5)))
            .with_retry(RetryConfig { max_retries: 1, retry_timeout: Duration::from_secs(5), ..Default::default() })
            .build().unwrap();
        Self { store, thread }
    }
    fn finish(self) { self.thread.join().unwrap(); }
}
fn response(status: u16, length: usize, body: &[u8], content_range: Option<&str>, etag: Option<&str>) -> Vec<u8> {
    let mut s = format!("HTTP/1.1 {status} Result\r\nContent-Length: {length}\r\nConnection: close\r\nContent-Type: application/octet-stream\r\nCache-Control: max-age=17\r\n");
    if let Some(x)=content_range {s.push_str(&format!("Content-Range: {x}\r\n"));}
    if let Some(x)=etag {s.push_str(&format!("ETag: {x}\r\n"));}
    s.push_str("\r\n"); let mut out=s.into_bytes(); out.extend_from_slice(body); out
}
fn reply(range: Option<GetRange>, status:u16, length:usize, body:&[u8], cr:Option<&str>, etag:Option<&str>) -> Reply {
    Reply {range:range.map(|r|r.to_string()),response:response(status,length,body,cr,etag),release:None}
}
fn opts(range:GetRange) -> GetOptions { GetOptions { range:Some(range), ..Default::default() } }
async fn accepted(range:GetRange, body:&[u8], cr:Option<&str>) {
    let server=Server::new(vec![reply(Some(range.clone()),200,body.len(),body,cr,Some("\"revision-a\""))]);
    let path=Path::from("table/data.parquet");
    let out=server.store.get_opts(&path,opts(range)).await.expect("whole-object 200 must be accepted");
    assert_eq!(out.range,0..body.len() as u64); assert_eq!(out.meta.size,body.len() as u64);
    assert_eq!(out.meta.location,path); assert_eq!(out.meta.e_tag.as_deref(),Some("\"revision-a\""));
    assert_eq!(out.attributes.get(&Attribute::ContentType).unwrap().as_ref(),"application/octet-stream");
    assert_eq!(out.attributes.get(&Attribute::CacheControl).unwrap().as_ref(),"max-age=17");
    assert_eq!(out.bytes().await.unwrap().as_ref(),body); server.finish();
}

#[tokio::test]
async fn bounded_whole_representation_and_clamped_end() {
    for len in [1,12,8193] {
        let body:Vec<u8>=(0..len).map(|i|((i*37+19)%251) as u8).collect();
        accepted(GetRange::Bounded(0..len as u64),&body,None).await;
        accepted(GetRange::Bounded(0..len as u64+23),&body,None).await;
    }
}
#[tokio::test]
async fn offset_and_suffix_covering_whole_representation() {
    for range in [GetRange::Offset(0),GetRange::Suffix(12),GetRange::Suffix(29)] {
        accepted(range,b"hello world!",None).await;
    }
}
#[tokio::test]
async fn stray_content_range_on_200_has_no_authority() {
    for cr in ["bytes 0-11/12","bytes 2-5/999","nonsense","bytes */12"] {
        accepted(GetRange::Bounded(0..12),b"hello world!",Some(cr)).await;
    }
}
#[tokio::test]
async fn ignored_true_subranges_remain_errors() {
    for range in [GetRange::Bounded(0..11),GetRange::Bounded(1..12),GetRange::Offset(1),GetRange::Suffix(11)] {
        let server=Server::new(vec![reply(Some(range.clone()),200,12,b"hello world!",Some("bytes 1-11/12"),None)]);
        assert!(server.store.get_opts(&Path::from("data"),opts(range)).await.is_err()); server.finish();
    }
}
#[tokio::test]
async fn genuine_partial_content_preserves_range_and_total_size() {
    for range in [GetRange::Bounded(3..8),GetRange::Suffix(9),GetRange::Offset(3)] {
        let expected=range.as_range(12).unwrap(); let data=&b"hello world!"[expected.start as usize..expected.end as usize];
        let cr=format!("bytes {}-{}/12",expected.start,expected.end-1);
        let server=Server::new(vec![reply(Some(range.clone()),206,data.len(),data,Some(&cr),None)]);
        let out=server.store.get_opts(&Path::from("data"),opts(range)).await.unwrap();
        assert_eq!(out.range,expected); assert_eq!(out.meta.size,12); assert_eq!(out.bytes().await.unwrap().as_ref(),data); server.finish();
    }
}
#[tokio::test]
async fn malformed_or_mismatched_partial_content_remains_error() {
    for cr in [None,Some("garbage"),Some("bytes 1-11/12")] {
        let range=GetRange::Bounded(0..12);
        let server=Server::new(vec![reply(Some(range.clone()),206,12,b"hello world!",cr,None)]);
        assert!(server.store.get_opts(&Path::from("data"),opts(range)).await.is_err()); server.finish();
    }
}
#[tokio::test]
async fn normal_get_and_head_are_unchanged() {
    let server=Server::new(vec![reply(None,200,12,b"hello world!",Some("ignored on 200"),Some("\"a\"")),reply(None,200,12,b"",None,Some("\"a\""))]);
    let out=server.store.get(&Path::from("data")).await.unwrap(); assert_eq!(out.range,0..12); assert_eq!(out.bytes().await.unwrap().as_ref(),b"hello world!");
    let meta=server.store.head(&Path::from("data")).await.unwrap(); assert_eq!(meta.size,12); server.finish();
}
#[tokio::test]
async fn full_response_with_truncated_body_does_not_succeed() {
    let range=GetRange::Bounded(0..12);
    let server=Server::new(vec![reply(Some(range.clone()),200,12,b"hello",None,None)]);
    let result=server.store.get_opts(&Path::from("data"),opts(range)).await;
    // On fixed code headers are valid, but collecting incomplete bytes fails.
    assert!(result.expect("valid full-range headers").bytes().await.is_err()); server.finish();
}
async fn retry_after_prefix(initial_status:u16,second_status:u16,second_etag:&str) -> object_store::Result<Vec<u8>> {
    let (tx,rx)=mpsc::channel();
    let mut first=reply(Some(GetRange::Bounded(0..12)),initial_status,12,b"hello",if initial_status==206 {Some("bytes 0-11/12")} else {None},Some("\"a\""));
    first.release=Some(rx);
    let second=if second_status==206 {reply(Some(GetRange::Bounded(5..12)),206,7,b" world!",Some("bytes 5-11/12"),Some(second_etag))}
        else {reply(Some(GetRange::Bounded(5..12)),200,12,b"hello world!",Some("bytes 5-11/12"),Some(second_etag))};
    let server=Server::new(vec![first,second]);
    let out=server.store.get_opts(&Path::from("data"),opts(GetRange::Bounded(0..12))).await?;
    assert_eq!(out.range,0..12); assert_eq!(out.meta.size,12);
    let mut stream=out.into_stream(); let prefix=stream.next().await.unwrap()?; assert_eq!(prefix.as_ref(),b"hello");
    tx.send(()).unwrap();
    let mut bytes=prefix.to_vec(); let mut error=None;
    while let Some(item)=stream.next().await { match item {Ok(b)=>bytes.extend_from_slice(&b),Err(e)=>{error=Some(e);break;}} }
    server.finish(); if let Some(e)=error {Err(e)} else {Ok(bytes)}
}
#[tokio::test]
async fn accepted_full_200_can_resume_with_partial_206_without_duplication() {
    assert_eq!(retry_after_prefix(200,206,"\"a\"").await.unwrap(),b"hello world!");
}
#[tokio::test]
async fn retry_cannot_accept_ignored_subrange_or_changed_object() {
    assert!(retry_after_prefix(206,200,"\"a\"").await.is_err());
    assert!(retry_after_prefix(206,206,"\"b\"").await.is_err());
    assert!(retry_after_prefix(200,206,"\"b\"").await.is_err());
}
#[tokio::test]
async fn retry_before_any_data_can_receive_whole_200() {
    let range=GetRange::Bounded(0..12);
    let server=Server::new(vec![reply(Some(range.clone()),200,12,b"",None,Some("\"a\"")),reply(Some(range.clone()),200,12,b"hello world!",None,Some("\"a\""))]);
    let out=server.store.get_opts(&Path::from("data"),opts(range)).await.unwrap();
    assert_eq!(out.bytes().await.unwrap().as_ref(),b"hello world!"); server.finish();
}
